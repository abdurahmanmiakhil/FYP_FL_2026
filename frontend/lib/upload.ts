/**
 * Resumable slide upload: the file is sent in chunks (PUT with Content-Range). If the network
 * drops, the uploader asks the server how many bytes it has and continues from there.
 */
import { API_PREFIX, apiFetch, toApiError } from "./api/client";

export const MAX_SLIDE_BYTES = 2 * 1024 ** 3;
export const ACCEPTED = [".tif", ".tiff", ".svs"];

export function checkSlideFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED.some((ext) => name.endsWith(ext)))
    return "Only .tif, .tiff and .svs whole-slide images are accepted.";
  if (file.size > MAX_SLIDE_BYTES) return "The slide is larger than 2 GB.";
  if (file.size === 0) return "The file is empty.";
  return null;
}

export interface UploadProgress {
  sent: number;
  total: number;
}

export interface UploadOptions {
  onProgress?: (p: UploadProgress) => void;
  signal?: AbortSignal;
  maxRetries?: number;
  /** override for tests */
  sleep?: (ms: number) => Promise<void>;
}

interface UploadState {
  upload_id: string;
  received_bytes: number;
  total_bytes: number;
  chunk_bytes: number;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

/** Upload `file`; resolves with the upload id once every byte is on the server. */
export async function uploadResumable(file: File, opts: UploadOptions = {}): Promise<string> {
  const { onProgress, signal, maxRetries = 5, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = opts;
  let state = await json<UploadState>(
    await apiFetch(`${API_PREFIX}/uploads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, size: file.size }),
      signal,
    }),
  );
  let offset = state.received_bytes;
  let failures = 0;
  onProgress?.({ sent: offset, total: file.size });
  while (offset < file.size) {
    signal?.throwIfAborted();
    const end = Math.min(offset + state.chunk_bytes, file.size);
    try {
      const res = await apiFetch(`${API_PREFIX}/uploads/${state.upload_id}`, {
        method: "PUT",
        headers: {
          "Content-Range": `bytes ${offset}-${end - 1}/${file.size}`,
          "Content-Type": "application/octet-stream",
        },
        body: file.slice(offset, end),
        signal,
      });
      if (res.status === 409) {
        // server has a different offset (e.g. a chunk was stored but the reply was lost): resync
        state = await json<UploadState>(
          await apiFetch(`${API_PREFIX}/uploads/${state.upload_id}`, { signal }),
        );
        offset = state.received_bytes;
        continue;
      }
      state = await json<UploadState>(res);
      if (
        !Number.isInteger(state.received_bytes) ||
        state.received_bytes <= offset ||
        state.received_bytes > file.size
      ) {
        throw new Error("The server did not confirm the uploaded chunk."); // never loop on a bad offset
      }
      offset = state.received_bytes;
      failures = 0;
      onProgress?.({ sent: offset, total: file.size });
    } catch (e) {
      if (signal?.aborted) throw e;
      if (
        e instanceof Error &&
        "status" in e &&
        (e as { status: number }).status < 500 &&
        (e as { status: number }).status !== 408
      )
        throw e; // client errors are not transient
      if (++failures > maxRetries) throw e;
      await sleep(Math.min(30_000, 1000 * 2 ** failures));
      const fresh = await apiFetch(`${API_PREFIX}/uploads/${state.upload_id}`, { signal }).catch(() => null);
      if (fresh?.ok) {
        state = (await fresh.json()) as UploadState;
        offset = state.received_bytes;
      }
    }
  }
  return state.upload_id;
}

export async function abortUpload(uploadId: string): Promise<void> {
  await apiFetch(`${API_PREFIX}/uploads/${uploadId}`, { method: "DELETE" }).catch(() => undefined);
}
