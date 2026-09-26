import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch, onSessionExpired, toApiError } from "@/lib/api/client";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("apiFetch", () => {
  it("sends the CSRF token on mutations only", async () => {
    document.cookie = "csrf_token=tok123";
    const fetchMock = vi.fn().mockResolvedValue(json(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("/api/v1/cases", { method: "POST" });
    await apiFetch("/api/v1/cases");
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get("X-CSRF-Token")).toBe("tok123");
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get("X-CSRF-Token")).toBeNull();
    expect(fetchMock.mock.calls[0][1].credentials).toBe("same-origin");
  });

  it("refreshes the session once on 401 and retries", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(401, { detail: "expired" }))
      .mockResolvedValueOnce(json(200, {})) // /auth/refresh
      .mockResolvedValueOnce(json(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await apiFetch("/api/v1/stats");
    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/refresh");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("reports an expired session when refresh fails", async () => {
    const expired = vi.fn();
    onSessionExpired(expired);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json(401, {})).mockResolvedValueOnce(json(401, {})));
    const res = await apiFetch("/api/v1/stats");
    expect(res.status).toBe(401);
    expect(expired).toHaveBeenCalledWith("expired");
  });

  it("never tries to refresh the login call", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(401, { detail: "Incorrect email or password." }));
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("/api/v1/auth/login", { method: "POST" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("toApiError", () => {
  it("reads string, structured and validation details", async () => {
    expect((await toApiError(json(423, { detail: "Account locked." }))).message).toBe("Account locked.");
    const otp = await toApiError(json(401, { detail: { code: "otp_required", message: "Enter the code" } }));
    expect(otp.code).toBe("otp_required");
    const v = await toApiError(
      json(422, { detail: "Invalid input.", errors: [{ field: "email", message: "bad" }] }),
    );
    expect(v.message).toContain("email bad");
    expect((await toApiError(new Response("oops", { status: 429 }))).message).toMatch(/Too many/);
  });
});

describe("toApiError with an already-consumed body", () => {
  it("uses the parsed error openapi-fetch returns", async () => {
    const res = json(401, { detail: "Incorrect email or password." });
    await res.text(); // body consumed, as openapi-fetch does
    expect((await toApiError(res, { detail: "Incorrect email or password." })).message).toBe(
      "Incorrect email or password.",
    );
  });
});
