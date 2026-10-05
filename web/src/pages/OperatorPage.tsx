import { cn } from "@/lib/utils.ts";
import { ApiError } from "@/lib/api.ts";
import {
  fetchBots,
  fetchCoins,
  logout,
  previewQuote,
  transitionBot,
  type Bot,
  type BotAction,
  type Coin,
  type QuotePreview,
  type Tick,
} from "@/lib/resources.ts";
import { useSession } from "@/session.ts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate } from "react-router-dom";

const ACTIONS: Record<BotAction, { label: string; statuses: string[] }> = {
  start: { label: "Start", statuses: ["paused", "stopped"] },
  pause: { label: "Pause", statuses: ["running"] },
  stop: { label: "Stop", statuses: ["running", "paused"] },
};

export function OperatorPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const me = session.data;
  const coins = useQuery({
    queryKey: ["coins"],
    queryFn: fetchCoins,
    enabled: me != null,
  });
  const bots = useQuery({
    queryKey: ["bots"],
    queryFn: fetchBots,
    enabled: me != null,
  });
  const signOut = useMutation({
    mutationFn: logout,
    onSettled: async () => {
      queryClient.setQueryData(["me"], null);
      await queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });

  if (session.isLoading) {
    return <Status text="Loading…" />;
  }
  if (session.isError) {
    return <Status text={messageOf(session.error)} />;
  }
  if (!me) {
    return <Navigate to="/login" replace />;
  }

  const canControl = me.permissions.includes("bots.control");
  const canReadTrades = me.permissions.includes("trades.read");

  return (
    <main className="min-h-svh bg-background text-foreground">
      <div className="mx-auto flex max-w-6xl flex-col gap-8 px-4 py-8">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-3xl font-semibold tracking-tight">CoinWatch</h1>
          <div className="flex items-center gap-3 text-sm">
            <p>
              Signed in as <span className="font-medium">{me.username}</span>
            </p>
            <button
              type="button"
              onClick={() => signOut.mutate()}
              disabled={signOut.isPending}
              className="rounded-md border px-3 py-1.5 disabled:opacity-50"
            >
              Log out
            </button>
          </div>
        </header>

        <section className="space-y-3">
          <h2 className="text-lg font-medium">Coins</h2>
          <CoinTable coins={coins.data} error={coins.error} loading={coins.isLoading} />
        </section>

        {canReadTrades ? (
          <section className="space-y-3">
            <h2 className="text-lg font-medium">Quote</h2>
            <QuoteForm coins={coins.data} />
          </section>
        ) : null}

        <section className="space-y-3">
          <h2 className="text-lg font-medium">Bots</h2>
          <BotTable
            bots={bots.data}
            error={bots.error}
            loading={bots.isLoading}
            canControl={canControl}
          />
        </section>
      </div>
    </main>
  );
}

function CoinTable({
  coins,
  error,
  loading,
}: {
  coins: Coin[] | undefined;
  error: unknown;
  loading: boolean;
}) {
  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading coins…</p>;
  }
  if (error) {
    return <p role="alert" className="text-sm text-destructive">{messageOf(error)}</p>;
  }
  if (!coins || coins.length === 0) {
    return <p className="text-sm text-muted-foreground">No coins.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[64rem] text-left text-sm">
        <thead className="bg-muted text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Name</th>
            <th className="px-3 py-2 font-medium">Symbol</th>
            <th className="px-3 py-2 font-medium">Address</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Price (SOL)</th>
            <th className="px-3 py-2 font-medium">Market cap (USD)</th>
            <th className="px-3 py-2 font-medium">SOL in curve</th>
            <th className="px-3 py-2 font-medium">Curve %</th>
          </tr>
        </thead>
        <tbody>
          {coins.map((coin) => (
            <tr key={`${coin.chain}:${coin.address}`} className="border-t">
              <td className="px-3 py-2">{coin.name}</td>
              <td className="px-3 py-2">{coin.symbol}</td>
              <td className="px-3 py-2 font-mono text-xs break-all">{coin.address}</td>
              <td className="px-3 py-2">{coin.status}</td>
              <td className="px-3 py-2">{tickField(coin.tick, "price_native")}</td>
              <td className="px-3 py-2">{tickField(coin.tick, "mcap_usd")}</td>
              <td className="px-3 py-2">{tickField(coin.tick, "liquidity_native")}</td>
              <td className="px-3 py-2">{curvePercent(coin.tick)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QuoteForm({ coins }: { coins: Coin[] | undefined }) {
  const firstAddress = coins?.[0]?.address ?? "";
  const [address, setAddress] = useState("");
  const [addressEdited, setAddressEdited] = useState(false);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [amount, setAmount] = useState("");
  const [positionSize, setPositionSize] = useState("");
  const [result, setResult] = useState<QuotePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const coinAddress = addressEdited ? address : firstAddress;

  async function onPreview() {
    setError(null);
    setResult(null);
    setPending(true);
    try {
      const preview = await previewQuote({
        side,
        coin_address: coinAddress,
        amount,
        position_size: side === "sell" ? positionSize : undefined,
      });
      setResult(preview);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void onPreview();
      }}
      className="max-w-xl space-y-3 rounded-xl border p-4"
    >
      <p className="text-sm text-muted-foreground">Preview only. Nothing is sent.</p>
      <label className="block space-y-1 text-sm">
        <span>Coin address</span>
        <input
          name="coin_address"
          list="quote-coins"
          value={coinAddress}
          onChange={(event) => {
            setAddressEdited(true);
            setAddress(event.target.value);
          }}
          className="w-full rounded-md border bg-background px-3 py-2 font-mono text-xs"
        />
        <datalist id="quote-coins">
          {(coins ?? []).map((coin) => (
            <option key={`${coin.chain}:${coin.address}`} value={coin.address}>
              {coin.symbol}
            </option>
          ))}
        </datalist>
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1 text-sm">
          <span>Side</span>
          <select
            name="side"
            value={side}
            onChange={(event) => {
              const next = event.target.value === "sell" ? "sell" : "buy";
              setSide(next);
            }}
            className="w-full rounded-md border bg-background px-3 py-2"
          >
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
        </label>
        <label className="block space-y-1 text-sm">
          <span>{side === "buy" ? "Amount (SOL)" : "Amount (tokens)"}</span>
          <input
            name="amount"
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            className="w-full rounded-md border bg-background px-3 py-2"
          />
        </label>
      </div>
      {side === "sell" ? (
        <label className="block space-y-1 text-sm">
          <span>Position size (tokens)</span>
          <input
            name="position_size"
            inputMode="decimal"
            value={positionSize}
            onChange={(event) => setPositionSize(event.target.value)}
            className="w-full rounded-md border bg-background px-3 py-2"
          />
        </label>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="rounded-md border px-3 py-1.5 disabled:opacity-50"
      >
        Preview
      </button>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {result ? (
        <dl className="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">Expected out</dt>
            <dd className="font-mono">{result.expected_out}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Minimum out</dt>
            <dd className="font-mono">{result.minimum_out}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Fee (SOL)</dt>
            <dd className="font-mono">{result.fee_native}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Price impact</dt>
            <dd className="font-mono">{result.price_impact_pct}%</dd>
          </div>
        </dl>
      ) : null}
    </form>
  );
}

function BotTable({
  bots,
  error,
  loading,
  canControl,
}: {
  bots: Bot[] | undefined;
  error: unknown;
  loading: boolean;
  canControl: boolean;
}) {
  const queryClient = useQueryClient();
  const [actionError, setActionError] = useState<{ id: number; message: string } | null>(null);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const change = useMutation({
    mutationFn: ({ id, action }: { id: number; action: BotAction }) => transitionBot(id, action),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["bots"] });
    },
  });

  async function onAction(id: number, action: BotAction) {
    setActionError(null);
    setPendingId(id);
    try {
      await change.mutateAsync({ id, action });
    } catch (caught) {
      setActionError({ id, message: messageOf(caught) });
    } finally {
      setPendingId(null);
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading bots…</p>;
  }
  if (error) {
    return <p role="alert" className="text-sm text-destructive">{messageOf(error)}</p>;
  }
  if (!bots || bots.length === 0) {
    return <p className="text-sm text-muted-foreground">No bots.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[40rem] text-left text-sm">
        <thead className="bg-muted text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Id</th>
            <th className="px-3 py-2 font-medium">Coin address</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Paper</th>
            {canControl ? <th className="px-3 py-2 font-medium">Actions</th> : null}
          </tr>
        </thead>
        <tbody>
          {bots.map((bot) => (
            <tr key={bot.id} className="border-t align-top">
              <td className="px-3 py-2">{bot.id}</td>
              <td className="px-3 py-2 font-mono text-xs break-all">{bot.coin_address}</td>
              <td className="px-3 py-2">{bot.status}</td>
              <td className="px-3 py-2">{bot.paper ? "yes" : "no"}</td>
              {canControl ? (
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-2">
                    {(Object.keys(ACTIONS) as BotAction[])
                      .filter((action) => ACTIONS[action].statuses.includes(bot.status))
                      .map((action) => (
                        <button
                          key={action}
                          type="button"
                          disabled={pendingId === bot.id}
                          onClick={() => void onAction(bot.id, action)}
                          className={cn(
                            "rounded-md border px-3 py-1.5",
                            pendingId === bot.id && "opacity-50",
                          )}
                        >
                          {ACTIONS[action].label}
                        </button>
                      ))}
                  </div>
                  {actionError?.id === bot.id ? (
                    <p role="alert" className="mt-2 text-destructive">
                      {actionError.message}
                    </p>
                  ) : null}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function tickField(tick: Tick | null, key: keyof Tick): string {
  if (!tick) {
    return "—";
  }
  return tick[key] ?? "—";
}

function curvePercent(tick: Tick | null): string {
  if (!tick || tick.curve_pct === null) {
    return "—";
  }
  return `${tick.curve_pct}%`;
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Request failed.";
}

function Status({ text }: { text: string }) {
  return (
    <main className="flex min-h-svh items-center justify-center bg-background text-foreground">
      <p>{text}</p>
    </main>
  );
}
