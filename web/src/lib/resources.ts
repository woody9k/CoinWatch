import {
  apiGet,
  apiPost,
  isRecord,
  optionalString,
  requireBoolean,
  requireNumber,
  requireString,
  requireStringList,
} from "@/lib/api.ts";

export type SessionUser = {
  username: string;
  role: string;
  permissions: string[];
};

export type Tick = {
  price_native: string | null;
  price_usd: string | null;
  mcap_usd: string | null;
  liquidity_native: string | null;
  curve_pct: string | null;
};

export type Coin = {
  chain: string;
  address: string;
  name: string;
  symbol: string;
  status: string;
  tick: Tick | null;
};

export type Bot = {
  id: number;
  chain: string;
  coin_address: string;
  strategy_id: number;
  wallet_id: number;
  status: string;
  paper: boolean;
  act_on_inference: boolean;
};

export type BotAction = "start" | "pause" | "stop";

export type Wallet = {
  id: number;
  chain: string;
  label: string;
  public_address: string;
};

export type Strategy = {
  id: number;
  name: string;
  yaml_config: string;
};

export type WalletCreate = {
  chain: string;
  label: string;
  public_address: string;
};

export type StrategyCreate = {
  name: string;
  yaml_config: string;
};

export type BotCreate = {
  chain: string;
  coin_address: string;
  strategy_id: number;
  wallet_id: number;
};

export async function fetchMe(): Promise<SessionUser> {
  const payload = await apiGet("/api/me");
  if (!isRecord(payload)) {
    throw unexpected();
  }
  return {
    username: requireString(payload, "username"),
    role: requireString(payload, "role"),
    permissions: requireStringList(payload, "permissions"),
  };
}

export async function login(username: string, password: string): Promise<void> {
  const payload = await apiPost("/api/login", { username, password });
  if (!isRecord(payload)) {
    throw unexpected();
  }
  requireString(payload, "username");
  requireString(payload, "role");
}

export async function logout(): Promise<void> {
  await apiPost("/api/logout");
}

export async function fetchCoins(): Promise<Coin[]> {
  const payload = await apiGet("/api/coins");
  if (!Array.isArray(payload)) {
    throw unexpected();
  }
  return payload.map(parseCoin);
}

export type Position = {
  bot_id: number;
  coin_address: string;
  size: string;
  cost_native: string;
  realized_pnl_native: string;
};

export type Trade = {
  id: number;
  ts: string;
  side: string;
  coin_address: string;
  amount_native: string;
  fee_native: string;
  price_impact_pct: string;
  paper: boolean;
};

export async function fetchPositions(): Promise<Position[]> {
  const payload = await apiGet("/api/positions");
  if (!Array.isArray(payload)) {
    throw unexpected();
  }
  return payload.map(parsePosition);
}

export async function fetchTrades(): Promise<Trade[]> {
  const payload = await apiGet("/api/trades");
  if (!Array.isArray(payload)) {
    throw unexpected();
  }
  return payload.map(parseTrade);
}

export async function fetchWallets(): Promise<Wallet[]> {
  const payload = await apiGet("/api/wallets");
  if (!Array.isArray(payload)) {
    throw unexpected();
  }
  return payload.map(parseWallet);
}

export async function fetchStrategies(): Promise<Strategy[]> {
  const payload = await apiGet("/api/strategies");
  if (!Array.isArray(payload)) {
    throw unexpected();
  }
  return payload.map(parseStrategy);
}

export async function createWallet(input: WalletCreate): Promise<Wallet> {
  const payload = await apiPost("/api/wallets", {
    chain: input.chain,
    label: input.label,
    public_address: input.public_address,
  });
  return parseWallet(payload);
}

export async function createStrategy(input: StrategyCreate): Promise<{ id: number; name: string }> {
  const payload = await apiPost("/api/strategies", {
    name: input.name,
    yaml_config: input.yaml_config,
  });
  if (!isRecord(payload)) {
    throw unexpected();
  }
  return {
    id: requireNumber(payload, "id"),
    name: requireString(payload, "name"),
  };
}

export async function createBot(input: BotCreate): Promise<Bot> {
  const payload = await apiPost("/api/bots", {
    chain: input.chain,
    coin_address: input.coin_address,
    strategy_id: input.strategy_id,
    wallet_id: input.wallet_id,
  });
  return parseBot(payload);
}

export async function fetchBots(): Promise<Bot[]> {
  const payload = await apiGet("/api/bots");
  if (!Array.isArray(payload)) {
    throw unexpected();
  }
  return payload.map(parseBot);
}

export async function transitionBot(id: number, action: BotAction): Promise<Bot> {
  const payload = await apiPost(`/api/bots/${id}/${action}`);
  return parseBot(payload);
}

export type QuotePreview = {
  side: string;
  amount_in: string;
  expected_out: string;
  minimum_out: string;
  fee_native: string;
  price_impact_pct: string;
  sol_debited: string;
  sol_credited: string;
};

export type QuoteRequest = {
  side: "buy" | "sell";
  coin_address: string;
  amount: string;
  position_size?: string;
};

export async function previewQuote(input: QuoteRequest): Promise<QuotePreview> {
  const body: Record<string, string> = {
    side: input.side,
    coin_address: input.coin_address,
    amount: input.amount,
  };
  if (input.side === "sell" && input.position_size) {
    body.position_size = input.position_size;
  }
  const payload = await apiPost("/api/quotes", body);
  return parseQuote(payload);
}

function parseCoin(value: unknown): Coin {
  if (!isRecord(value)) {
    throw unexpected();
  }
  return {
    chain: requireString(value, "chain"),
    address: requireString(value, "address"),
    name: requireString(value, "name"),
    symbol: requireString(value, "symbol"),
    status: requireString(value, "status"),
    tick: parseTick(value.tick),
  };
}

function parseTick(value: unknown): Tick | null {
  if (value === null) {
    return null;
  }
  if (!isRecord(value)) {
    throw unexpected();
  }
  return {
    price_native: optionalString(value, "price_native"),
    price_usd: optionalString(value, "price_usd"),
    mcap_usd: optionalString(value, "mcap_usd"),
    liquidity_native: optionalString(value, "liquidity_native"),
    curve_pct: optionalString(value, "curve_pct"),
  };
}

function parsePosition(value: unknown): Position {
  if (!isRecord(value)) {
    throw unexpected();
  }
  return {
    bot_id: requireNumber(value, "bot_id"),
    coin_address: requireString(value, "coin_address"),
    size: requireString(value, "size"),
    cost_native: requireString(value, "cost_native"),
    realized_pnl_native: requireString(value, "realized_pnl_native"),
  };
}

function parseTrade(value: unknown): Trade {
  if (!isRecord(value)) {
    throw unexpected();
  }
  return {
    id: requireNumber(value, "id"),
    ts: requireString(value, "ts"),
    side: requireString(value, "side"),
    coin_address: requireString(value, "coin_address"),
    amount_native: requireString(value, "amount_native"),
    fee_native: requireString(value, "fee_native"),
    price_impact_pct: requireString(value, "price_impact_pct"),
    paper: requireBoolean(value, "paper"),
  };
}

function parseWallet(value: unknown): Wallet {
  if (!isRecord(value)) {
    throw unexpected();
  }
  return {
    id: requireNumber(value, "id"),
    chain: requireString(value, "chain"),
    label: requireString(value, "label"),
    public_address: requireString(value, "public_address"),
  };
}

function parseStrategy(value: unknown): Strategy {
  if (!isRecord(value)) {
    throw unexpected();
  }
  return {
    id: requireNumber(value, "id"),
    name: requireString(value, "name"),
    yaml_config: requireString(value, "yaml_config"),
  };
}

function parseBot(value: unknown): Bot {
  if (!isRecord(value)) {
    throw unexpected();
  }
  return {
    id: requireNumber(value, "id"),
    chain: requireString(value, "chain"),
    coin_address: requireString(value, "coin_address"),
    strategy_id: requireNumber(value, "strategy_id"),
    wallet_id: requireNumber(value, "wallet_id"),
    status: requireString(value, "status"),
    paper: requireBoolean(value, "paper"),
    act_on_inference: requireBoolean(value, "act_on_inference"),
  };
}

function parseQuote(value: unknown): QuotePreview {
  if (!isRecord(value)) {
    throw unexpected();
  }
  return {
    side: requireString(value, "side"),
    amount_in: requireString(value, "amount_in"),
    expected_out: requireString(value, "expected_out"),
    minimum_out: requireString(value, "minimum_out"),
    fee_native: requireString(value, "fee_native"),
    price_impact_pct: requireString(value, "price_impact_pct"),
    sol_debited: requireString(value, "sol_debited"),
    sol_credited: requireString(value, "sol_credited"),
  };
}

function unexpected(): Error {
  return new Error("Unexpected response.");
}
