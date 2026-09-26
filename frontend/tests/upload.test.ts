import { afterEach, describe, expect, it, vi } from "vitest";

import { checkSlideFile, uploadResumable } from "@/lib/upload";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** end byte of a "bytes a-b/total" Content-Range header */
function rangeEnd(h: string | null): number {
  const m = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(h ?? "");
  if (!m) throw new Error(`bad Content-Range ${h}`);
  return Number(m[2]);
}

function file(size: number, name = "slide.tiff") {
  return new File([new Uint8Array(size)], name);
}

afterEach(() => vi.unstubAllGlobals());

describe("checkSlideFile", () => {
  it("accepts slides and rejects other files", () => {
    expect(checkSlideFile(file(10, "a.SVS"))).toBeNull();
    expect(checkSlideFile(file(10, "a.tif"))).toBeNull();
    expect(checkSlideFile(file(10, "a.png"))).toMatch(/Only/);
    expect(checkSlideFile(file(0, "a.tiff"))).toMatch(/empty/);
  });
});

describe("uploadResumable", () => {
  it("sends every chunk with Content-Range and reports progress", async () => {
    const calls: { method: string; range: string | null }[] = [];
    let received = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        const method = init.method ?? "GET";
        const range = new Headers(init.headers).get("Content-Range");
        calls.push({ method, range });
        if (method === "POST")
          return json(201, { upload_id: "u1", received_bytes: 0, total_bytes: 25, chunk_bytes: 10 });
        received = rangeEnd(range) + 1;
        return json(200, { upload_id: "u1", received_bytes: received, total_bytes: 25, chunk_bytes: 10 });
      }),
    );
    const progress: number[] = [];
    const id = await uploadResumable(file(25), { onProgress: (p) => progress.push(p.sent) });
    expect(id).toBe("u1");
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.range)).toEqual([
      "bytes 0-9/25",
      "bytes 10-19/25",
      "bytes 20-24/25",
    ]);
    expect(progress.at(-1)).toBe(25);
  });

  it("resumes from the server offset after a network error", async () => {
    let putCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        const method = init.method ?? "GET";
        if (method === "POST")
          return json(201, { upload_id: "u2", received_bytes: 0, total_bytes: 20, chunk_bytes: 10 });
        if (method === "GET")
          return json(200, { upload_id: "u2", received_bytes: 10, total_bytes: 20, chunk_bytes: 10 });
        putCount++;
        if (putCount === 2) throw new TypeError("network down");
        const end = rangeEnd(new Headers(init.headers).get("Content-Range"));
        return json(200, { upload_id: "u2", received_bytes: end + 1, total_bytes: 20, chunk_bytes: 10 });
      }),
    );
    const id = await uploadResumable(file(20), { sleep: async () => undefined });
    expect(id).toBe("u2");
    expect(putCount).toBe(3);
  });

  it("gives up instead of looping when the server never confirms a chunk", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) =>
        (init.method ?? "GET") === "POST"
          ? json(201, { upload_id: "u3", received_bytes: 0, total_bytes: 20, chunk_bytes: 10 })
          : json(200, { upload_id: "u3", received_bytes: 0, total_bytes: 20, chunk_bytes: 10 }),
      ),
    );
    await expect(uploadResumable(file(20), { sleep: async () => undefined, maxRetries: 2 })).rejects.toThrow(
      /did not confirm/,
    );
  });

  it("does not retry client errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) =>
        init.method === "POST"
          ? json(415, { detail: "Only .tif, .tiff and .svs slides are accepted." })
          : json(200, {}),
      ),
    );
    await expect(uploadResumable(file(5))).rejects.toThrow(/Only .tif/);
  });
});
