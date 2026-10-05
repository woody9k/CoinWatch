"""Edge-triggered strategy rules.

A document is a mapping with a ``rules`` list. Each trigger is one comparison.
Rules are never passed to ``eval`` or ``exec``. A rule fires on the tick it
becomes true, then stays armed until a tick where it is false.
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import yaml

from coinwatch.errors import StrategyError

_FORBIDDEN = ("eval", "exec", "__", "import")
_ACTIONS = ("buy", "sell_pct", "sell_all", "stop_loss")
_OPERATORS = (">=", "<=", "==", "!=", ">", "<")
_PROTECTIVE_RANK = {"stop_loss": 0, "sell_all": 1, "sell_pct": 2, "buy": 3}

Action = Literal["buy", "sell_pct", "sell_all", "stop_loss"]
Operator = Literal[">", "<", ">=", "<=", "==", "!="]
NumericField = Literal["mcap_usd", "price_change_pct_5m", "curve_pct"]


@dataclass(frozen=True, slots=True)
class Rule:
    """One comparison and the action it may fire.

    ``amount_native`` is set only for ``buy``. ``pct`` is set only for
    ``sell_pct``. A document ``note`` is not stored.
    """

    field: NumericField | Literal["dev_sold"]
    operator: Operator
    literal: Decimal | bool
    action: Action
    amount_native: Decimal | None
    pct: Decimal | None


@dataclass(frozen=True, slots=True)
class Strategy:
    """Rules in document order. An index here is the index ``evaluate`` uses."""

    rules: tuple[Rule, ...]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Fields a trigger can read. ``None`` makes every comparison on that field false."""

    mcap_usd: Decimal | None
    price_change_pct_5m: Decimal | None
    curve_pct: Decimal | None
    dev_sold: bool | None


@dataclass(frozen=True, slots=True)
class FiredRule:
    """The action selected for this tick.

    ``amount_native`` is set only for ``buy``. ``pct`` is set only for ``sell_pct``.
    """

    index: int
    action: Action
    amount_native: Decimal | None
    pct: Decimal | None


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One tick. ``armed`` is every rule index whose comparison is true now."""

    winner: FiredRule | None
    armed: frozenset[int]


def load_strategy(text: str) -> Strategy:
    """Parse strategy YAML into rules.

    The text is rejected before parsing when it contains ``eval``, ``exec``,
    ``__``, or ``import``. The document must be a mapping with a ``rules``
    list. Each rule needs a single comparison trigger and an action.
    ``buy`` requires ``amount_native``. ``sell_pct`` requires ``pct`` from
    1 to 100. ``dev_sold`` compares only to ``true`` or ``false``.
    """
    for token in _FORBIDDEN:
        if token in text:
            raise StrategyError("strategy text contains a forbidden token")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise StrategyError("strategy YAML could not be read") from exc
    if not isinstance(document, dict) or not isinstance(document.get("rules"), list):
        raise StrategyError("strategy must be a mapping with a rules list")
    rules = tuple(_parse_rule(item) for item in document["rules"])
    return Strategy(rules)


def evaluate(strategy: Strategy, snapshot: Snapshot, armed: frozenset[int]) -> Evaluation:
    """Return the most protective newly true rule and the next armed set.

    A rule fires only when its comparison holds and its index is not in
    ``armed``. The next armed set is every index that is true now, so a
    condition that stays true does not fire again until a tick where it is
    false. When several rules fire, ``stop_loss`` wins over ``sell_all``,
    then ``sell_pct``, then ``buy``. The same action keeps the earlier index.
    """
    true_now: list[int] = []
    firing: list[tuple[int, Rule]] = []
    for index, rule in enumerate(strategy.rules):
        if not _holds(rule, snapshot):
            continue
        true_now.append(index)
        if index not in armed:
            firing.append((index, rule))
    winner = _select(firing)
    return Evaluation(winner, frozenset(true_now))


def _parse_rule(item: object) -> Rule:
    if not isinstance(item, dict):
        raise StrategyError("each rule must be a mapping")
    trigger = item.get("trigger")
    action = item.get("action")
    if not isinstance(trigger, str) or not isinstance(action, str):
        raise StrategyError("each rule needs a trigger and an action")
    if action not in _ACTIONS:
        raise StrategyError("unknown action")
    if "note" in item and not isinstance(item["note"], str):
        raise StrategyError("note must be a string")
    field, operator, literal = _parse_trigger(trigger)
    amount_native = _amount(item) if action == "buy" else None
    pct = _pct(item) if action == "sell_pct" else None
    return Rule(field, operator, literal, action, amount_native, pct)


def _parse_trigger(
    trigger: str,
) -> tuple[NumericField | Literal["dev_sold"], Operator, Decimal | bool]:
    text = trigger.strip()
    if not text or any(mark in text for mark in ("(", ")", " and ", " or ")):
        raise StrategyError("trigger must be one comparison")
    field_text, operator, literal_text = _split_comparison(text)
    if field_text == "dev_sold":
        if literal_text not in ("true", "false"):
            raise StrategyError("dev_sold compares only to true or false")
        return "dev_sold", operator, literal_text == "true"
    field = _numeric_field(field_text)
    if not _fullmatch_number(literal_text):
        raise StrategyError("numeric literal required")
    return field, operator, Decimal(literal_text)


def _numeric_field(field_text: str) -> NumericField:
    if field_text == "mcap_usd":
        return "mcap_usd"
    if field_text == "price_change_pct_5m":
        return "price_change_pct_5m"
    if field_text == "curve_pct":
        return "curve_pct"
    raise StrategyError("unknown field")


def _split_comparison(text: str) -> tuple[str, Operator, str]:
    """Split on the first operator that leaves a single field and a single literal.

    Longer operators are tried first so ``>=`` is not read as ``>``.
    """
    for operator in _OPERATORS:
        field, separator, literal = text.partition(operator)
        if separator != operator:
            continue
        field = field.strip()
        literal = literal.strip()
        if field and literal and " " not in field and " " not in literal:
            return field, operator, literal
    raise StrategyError("bad operator")


def _fullmatch_number(text: str) -> bool:
    if not text:
        return False
    body = text[1:] if text[0] in "+-" else text
    if not body or body.count(".") > 1:
        return False
    whole, dot, frac = body.partition(".")
    if not dot:
        return whole.isdigit()
    return (whole.isdigit() or whole == "") and (frac.isdigit() or frac == "") and body != "."


def _amount(item: dict[str, object]) -> Decimal:
    if "amount_native" not in item:
        raise StrategyError("buy requires amount_native")
    return _decimal_number(item["amount_native"], "amount_native")


def _pct(item: dict[str, object]) -> Decimal:
    if "pct" not in item:
        raise StrategyError("sell_pct requires pct")
    pct = _decimal_number(item["pct"], "pct")
    if pct < 1 or pct > 100:
        raise StrategyError("pct must be from 1 to 100")
    return pct


def _decimal_number(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StrategyError(f"{label} must be a number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StrategyError(f"{label} must be a number")
        return Decimal(str(value))
    return Decimal(value)


def _holds(rule: Rule, snapshot: Snapshot) -> bool:
    current = getattr(snapshot, rule.field)
    if current is None:
        return False
    if rule.field == "dev_sold":
        if not isinstance(current, bool) or not isinstance(rule.literal, bool):
            return False
        return _compare_bool(current, rule.operator, rule.literal)
    if not isinstance(current, Decimal) or not isinstance(rule.literal, Decimal):
        return False
    return _compare_decimal(current, rule.operator, rule.literal)


def _compare_decimal(left: Decimal, operator: Operator, right: Decimal) -> bool:
    if operator == ">":
        return left > right
    if operator == "<":
        return left < right
    if operator == ">=":
        return left >= right
    if operator == "<=":
        return left <= right
    if operator == "==":
        return left == right
    return left != right


def _compare_bool(left: bool, operator: Operator, right: bool) -> bool:
    if operator == ">":
        return left > right
    if operator == "<":
        return left < right
    if operator == ">=":
        return left >= right
    if operator == "<=":
        return left <= right
    if operator == "==":
        return left == right
    return left != right


def _select(firing: list[tuple[int, Rule]]) -> FiredRule | None:
    if not firing:
        return None
    index, rule = min(firing, key=lambda item: (_PROTECTIVE_RANK[item[1].action], item[0]))
    return FiredRule(index, rule.action, rule.amount_native, rule.pct)
