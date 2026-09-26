/**
 * Typed API client (openapi-fetch over the generated OpenAPI schema).
 *
 * - Same origin: `/api/v1/...` (proxied to FastAPI), cookies are httpOnly and sent automatically.
 * - CSRF: mutations send the double-submit token from the `csrf_token` cookie.
 * - Silent refresh: on 401 the client calls /auth/refresh once (shared by concurrent requests)
 *   and retries; if that fails the session is over and `onSessionExpired` fires.
 */
import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type User = Schemas["UserOut"];
export type SessionOut = Schemas["SessionOut"];
export type CaseSummary = Schemas["CaseSummary"];
export type CaseDetail = Schemas["CaseDetail"];
export type Prediction = Schemas["PredictionOut"];
export type Review = Schemas["ReviewOut"];
export type Stats = Schemas["Stats"];
export type AuditEntry = Schemas["AuditOut"];
export type ModelCard = Schemas["ModelCard"];
export type Health = Schemas["Health"];
export type Role = User["role"];
export type CaseStatus = CaseSummary["status"];
export type Decision = Review["decision"];

export const API_PREFIX = "/api/v1";
const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);
const NO_REFRESH = ["/auth/login", "/auth/refresh", "/auth/logout"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const hit = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : undefined;
}

export function csrfHeaders(): Record<string, string> {
  const token = readCookie("csrf_token");
  return token ? { "X-CSRF-Token": token } : {};
}

let sessionExpiredHandler: (reason: "expired" | "idle") => void = () => undefined;
export function onSessionExpired(fn: (reason: "expired" | "idle") => void): void {
  sessionExpiredHandler = fn;
}

let refreshing: Promise<boolean> | null = null;

/** Rotate the refresh token. Concurrent callers share one request. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= fetch(`${API_PREFIX}/auth/refresh`, {
    method: "POST",
    credentials: "same-origin",
    headers: csrfHeaders(),
  })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

/**
 * Turn any error body the API sends into a readable message. Pass `parsed` when the body was
 * already read (openapi-fetch consumes it and returns it as `error`).
 */
export async function toApiError(res: Response, parsed?: unknown): Promise<ApiError> {
  let body: unknown = parsed ?? null;
  if (parsed === undefined) {
    try {
      body = await res.clone().json();
    } catch {
      /* not JSON, or already consumed */
    }
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    const d = detail as { message: string; code?: string };
    return new ApiError(res.status, d.message, d.code, detail);
  }
  if (typeof detail === "string") {
    const errors = (body as { errors?: { field: string; message: string }[] }).errors;
    const extra = errors?.length ? `: ${errors.map((e) => `${e.field} ${e.message}`).join("; ")}` : "";
    return new ApiError(res.status, detail + extra, undefined, body);
  }
  const fallback: Record<number, string> = {
    401: "Your session has ended. Please sign in again.",
    403: "You do not have permission to do this.",
    404: "Not found.",
    413: "The file is too large.",
    429: "Too many requests - please wait a moment.",
  };
  return new ApiError(res.status, fallback[res.status] ?? `Request failed (${res.status}).`);
}

/** fetch with CSRF, same-origin cookies and one silent refresh on 401. */
export async function apiFetch(input: string, init: RequestInit = {}, retried = false): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!SAFE.has(method)) Object.entries(csrfHeaders()).forEach(([k, v]) => headers.set(k, v));
  const res = await fetch(input, { ...init, headers, credentials: "same-origin" });
  const path = input.replace(API_PREFIX, "");
  if (res.status === 401 && !retried && !NO_REFRESH.some((p) => path.startsWith(p))) {
    if (await refreshSession()) return apiFetch(input, init, true);
    sessionExpiredHandler("expired");
  }
  return res;
}

// untouched copies of outgoing requests, so a 401 can be retried after the body was consumed
const pending = new Map<string, Request>();

const refreshMiddleware: Middleware = {
  async onRequest({ request, id }) {
    if (!SAFE.has(request.method)) {
      Object.entries(csrfHeaders()).forEach(([k, v]) => request.headers.set(k, v));
    }
    pending.set(id, request.clone());
    return request;
  },
  async onResponse({ request, response, id }) {
    const original = pending.get(id);
    pending.delete(id);
    const path = new URL(request.url).pathname.replace(API_PREFIX, "");
    if (response.status !== 401 || !original || NO_REFRESH.some((p) => path.startsWith(p))) return response;
    if (!(await refreshSession())) {
      sessionExpiredHandler("expired");
      return response;
    }
    Object.entries(csrfHeaders()).forEach(([k, v]) => original.headers.set(k, v));
    return fetch(original);
  },
  onError({ id }) {
    pending.delete(id);
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "same-origin" });
api.use(refreshMiddleware);

/** Unwrap an openapi-fetch result: return data or throw a readable ApiError. */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (!response.ok) throw await toApiError(response, error);
  return data as T;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong.";
}
