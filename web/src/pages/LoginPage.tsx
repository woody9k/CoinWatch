import { ApiError } from "@/lib/api.ts";
import { login } from "@/lib/resources.ts";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { useSession } from "@/session.ts";

export function LoginPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const signIn = useMutation({
    gcTime: 0,
    mutationFn: (credentials: { username: string; password: string }) =>
      login(credentials.username, credentials.password),
    onSuccess: async () => {
      setPassword("");
      await queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });

  if (session.isLoading) {
    return (
      <main className="flex min-h-svh items-center justify-center bg-background text-foreground">
        <p>Loading…</p>
      </main>
    );
  }
  if (session.data) {
    return <Navigate to="/" replace />;
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    signIn.mutate({ username, password });
  }

  const message = signIn.error instanceof ApiError ? signIn.error.message : null;

  return (
    <main className="flex min-h-svh items-center justify-center bg-background px-4 text-foreground">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-xl border bg-card p-6 text-card-foreground shadow-sm"
      >
        <h1 className="text-2xl font-semibold tracking-tight">CoinWatch</h1>
        <label className="block space-y-1 text-sm">
          <span>Username</span>
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            className="w-full rounded-md border bg-background px-3 py-2"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span>Password</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            className="w-full rounded-md border bg-background px-3 py-2"
          />
        </label>
        {message ? (
          <p role="alert" className="text-sm text-destructive">
            {message}
          </p>
        ) : null}
        {signIn.error && !message ? (
          <p role="alert" className="text-sm text-destructive">
            Request failed.
          </p>
        ) : null}
        <button
          type="submit"
          disabled={signIn.isPending}
          className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          Sign in
        </button>
      </form>
    </main>
  );
}
