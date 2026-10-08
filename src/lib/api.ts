// Frontend client for the FastAPI backend, always hitting `/api/proxy/...`.
// In dev, Vite's `server.proxy` forwards to localhost:8000 directly.
// In prod (Cloudflare Pages), `functions/api/proxy/[[path]].ts` adds the
// Authorization header. Either way, the browser only ever sees same-origin
// URLs and never carries a token.

import type {
  CreateJobRequest,
  CreateJobResponse,
  JobSnapshot,
} from "./types";

const BASE = "/api/proxy";

async function asJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail: unknown = await resp.text().catch(() => "");
    try {
      detail = JSON.parse(detail as string);
    } catch {
      // leave as text
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

export class ApiError extends Error {
  public readonly status: number;
  public readonly detail: unknown;
  constructor(status: number, detail: unknown) {
    super(`API ${status}: ${JSON.stringify(detail)}`);
    this.status = status;
    this.detail = detail;
  }
}

export async function createJob(
  req: CreateJobRequest,
): Promise<CreateJobResponse> {
  const resp = await fetch(`${BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return asJson<CreateJobResponse>(resp);
}

export async function getJob(jobId: string): Promise<JobSnapshot> {
  const resp = await fetch(`${BASE}/jobs/${jobId}`);
  return asJson<JobSnapshot>(resp);
}

export function streamUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/stream`;
}

/**
 * Ask the backend to terminate a running job and free the single job slot.
 * Answers 202 — the runner only flags the job, and the pump thread publishes
 * the terminal `cancelled` status over SSE once the worker process is dead.
 */
export async function cancelJob(
  jobId: string,
): Promise<{ job_id: string; cancel_requested: boolean }> {
  const resp = await fetch(`${BASE}/jobs/${jobId}/cancel`, { method: "POST" });
  return asJson<{ job_id: string; cancel_requested: boolean }>(resp);
}

export function resultUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/result`;
}

export async function downloadResult(jobId: string): Promise<void> {
  const resp = await fetch(resultUrl(jobId));
  if (!resp.ok) {
    throw new ApiError(resp.status, await resp.text().catch(() => ""));
  }
  const blob = await resp.blob();
  const filename =
    parseFilename(resp.headers.get("Content-Disposition")) ??
    `master_summary_${jobId.slice(0, 8)}.xlsx`;
  triggerDownload(blob, filename);
}

function parseFilename(disposition: string | null): string | null {
  if (!disposition) return null;
  const m = disposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?/i);
  return m ? decodeURIComponent(m[1]) : null;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
