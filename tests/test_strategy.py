"""Strategy load and edge evaluation with no network."""

from decimal import Decimal

import pytest

import coinwatch.strategy as strategy_module
from coinwatch.errors import StrategyError
from coinwatch.strategy import Snapshot, evaluate, load_strategy

_SPEC_SAMPLE = """
strategy: default
chain: solana
coin: BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump
rules:
  - trigger: mcap_usd > 10000
    action: buy
    amount_native: 0.05
  - trigger: price_change_pct_5m > 20
    action: sell_pct
    pct: 50
  - trigger: price_change_pct_5m < -15
    action: stop_loss
  - trigger: curve_pct > 95
    action: sell_all
  - trigger: dev_sold == true
    action: sell_all
    note: "dev rug flag"
"""

_SENTINEL = "coinwatch_strategy_sentinel"


def _snapshot(
    *,
    mcap_usd: Decimal | None = None,
    price_change_pct_5m: Decimal | None = None,
    curve_pct: Decimal | None = None,
    dev_sold: bool | None = None,
) -> Snapshot:
    return Snapshot(mcap_usd, price_change_pct_5m, curve_pct, dev_sold)


def test_spec_sample_loads() -> None:
    """The section 4.2 sample parses, and its note is not an action input."""
    strategy = load_strategy(_SPEC_SAMPLE)
    assert [rule.action for rule in strategy.rules] == [
        "buy",
        "sell_pct",
        "stop_loss",
        "sell_all",
        "sell_all",
    ]
    assert strategy.rules[0].amount_native == Decimal("0.05")
    assert strategy.rules[0].pct is None
    assert strategy.rules[1].pct == Decimal(50)
    assert strategy.rules[1].amount_native is None
    assert strategy.rules[2].literal == Decimal(-15)
    assert strategy.rules[4].literal is True


def test_mcap_buy_fires_on_each_new_cross() -> None:
    """A level buys once, stays quiet while it holds, and can fire after it clears."""
    strategy = load_strategy(
        """
        rules:
          - trigger: mcap_usd > 10000
            action: buy
            amount_native: 0.05
        """
    )
    above = _snapshot(mcap_usd=Decimal("10000.01"))
    first = evaluate(strategy, above, frozenset())
    assert first.winner is not None
    assert first.winner.index == 0
    assert first.winner.action == "buy"
    assert first.winner.amount_native == Decimal("0.05")
    assert first.winner.pct is None
    assert first.armed == frozenset({0})

    still = evaluate(strategy, _snapshot(mcap_usd=Decimal(20000)), first.armed)
    assert still.winner is None
    assert still.armed == frozenset({0})

    cleared = evaluate(strategy, _snapshot(mcap_usd=Decimal(10000)), still.armed)
    assert cleared.winner is None
    assert cleared.armed == frozenset()

    again = evaluate(strategy, above, cleared.armed)
    assert again.winner is not None
    assert again.winner.action == "buy"
    assert again.armed == frozenset({0})


def test_dev_sold_beats_a_new_buy() -> None:
    """Two new matches on one tick keep the more protective action."""
    strategy = load_strategy(
        """
        rules:
          - trigger: mcap_usd > 10000
            action: buy
            amount_native: 0.05
          - trigger: dev_sold == true
            action: sell_all
        """
    )
    result = evaluate(
        strategy,
        _snapshot(mcap_usd=Decimal(10001), dev_sold=True),
        frozenset(),
    )
    assert result.winner is not None
    assert result.winner.action == "sell_all"
    assert result.winner.index == 1
    assert result.winner.amount_native is None
    assert result.winner.pct is None
    assert result.armed == frozenset({0, 1})


def test_missing_curve_does_not_fire() -> None:
    """A missing field is false for every operator, including a level cross."""
    strategy = load_strategy(
        """
        rules:
          - trigger: curve_pct > 95
            action: sell_all
        """
    )
    result = evaluate(strategy, _snapshot(curve_pct=None), frozenset())
    assert result.winner is None
    assert result.armed == frozenset()


@pytest.mark.parametrize(
    "text",
    [
        "eval('globals().__setitem__(\"coinwatch_strategy_sentinel\", True)')",
        "rules:\n  - trigger: __import__('os')\n    action: sell_all\n",
    ],
)
def test_forbidden_text_raises_without_running(text: str) -> None:
    """Forbidden tokens raise StrategyError and do not run the document."""
    with pytest.raises(StrategyError):
        load_strategy(text)
    assert _SENTINEL not in globals()
    assert _SENTINEL not in vars(strategy_module)


def test_compound_operator_stays_intact() -> None:
    """``>=`` is one operator, including when the trigger has no spaces."""
    strategy = load_strategy(
        """
        rules:
          - trigger: mcap_usd>=10000
            action: buy
            amount_native: 1
        """
    )
    rule = strategy.rules[0]
    assert rule.operator == ">="
    assert rule.literal == Decimal(10000)
    fired = evaluate(strategy, _snapshot(mcap_usd=Decimal(10000)), frozenset())
    assert fired.winner is not None
    assert fired.winner.action == "buy"


def test_stop_loss_beats_sell_all() -> None:
    """The protective order is stop_loss, then sell_all, then sell_pct, then buy."""
    strategy = load_strategy(
        """
        rules:
          - trigger: dev_sold == true
            action: sell_all
          - trigger: price_change_pct_5m < -15
            action: stop_loss
        """
    )
    result = evaluate(
        strategy,
        _snapshot(price_change_pct_5m=Decimal(-16), dev_sold=True),
        frozenset(),
    )
    assert result.winner is not None
    assert result.winner.action == "stop_loss"
    assert result.winner.index == 1


def test_sell_pct_without_pct_is_rejected() -> None:
    """sell_pct has no default size."""
    with pytest.raises(StrategyError):
        load_strategy(
            """
            rules:
              - trigger: price_change_pct_5m > 20
                action: sell_pct
            """
        )
