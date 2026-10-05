export class ApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

const REQUEST_HEADER = "X-CoinWatch-Request";

export async function apiGet(path: string): Promise<unknown> {
  const response = await fetch(path, { credentials: "include" });
  return readBody(response);
}

export async function apiPost(path: string, body?: unknown): Promise<unknown> {
  const headers = new Headers({ [REQUEST_HEADER]: "1" });
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return readBody(response);
}

async function readBody(response: Response): Promise<unknown> {
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw errorFrom(payload);
  }
  return payload;
}

function errorFrom(payload: unknown): ApiError {
  if (!isRecord(payload) || !isRecord(payload.error)) {
    return new ApiError("http_error", "Request failed.");
  }
  const { code, message } = payload.error;
  if (typeof code === "string" && typeof message === "string") {
    return new ApiError(code, message);
  }
  return new ApiError("http_error", "Request failed.");
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function requireString(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  if (typeof value !== "string") {
    throw new ApiError("invalid_response", "Unexpected response.");
  }
  return value;
}

export function requireNumber(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  if (typeof value !== "number") {
    throw new ApiError("invalid_response", "Unexpected response.");
  }
  return value;
}

export function requireBoolean(record: Record<string, unknown>, key: string): boolean {
  const value = record[key];
  if (typeof value !== "boolean") {
    throw new ApiError("invalid_response", "Unexpected response.");
  }
  return value;
}

export function optionalString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  if (value === null) {
    return null;
  }
  if (typeof value !== "string") {
    throw new ApiError("invalid_response", "Unexpected response.");
  }
  return value;
}

export function requireStringList(record: Record<string, unknown>, key: string): string[] {
  const value = record[key];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new ApiError("invalid_response", "Unexpected response.");
  }
  return value;
}
