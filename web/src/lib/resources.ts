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

function unexpected(): Error {
  return new Error("Unexpected response.");
}
