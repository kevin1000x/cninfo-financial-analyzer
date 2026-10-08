import { authenticatedFetch } from "./auth";
import { readSse, type StreamEvent } from "./sse";
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
  const resp = await authenticatedFetch(`${BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return asJson<CreateJobResponse>(resp);
}

export async function getJob(jobId: string): Promise<JobSnapshot> {
  const resp = await authenticatedFetch(`${BASE}/jobs/${jobId}`);
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
  const resp = await authenticatedFetch(`${BASE}/jobs/${jobId}/cancel`, { method: "POST" });
  return asJson<{ job_id: string; cancel_requested: boolean }>(resp);
}

export function resultUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/result`;
}

export async function downloadResult(jobId: string): Promise<void> {
  const resp = await authenticatedFetch(resultUrl(jobId));
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


export async function streamJob(jobId: string, signal: AbortSignal, onEvent: (event: StreamEvent) => boolean | void, onConnection: (message: string | null) => void): Promise<void> {
  let lastId = "";
  for (let attempt = 0; !signal.aborted; attempt++) {
    let ended = false;
    try {
      const response = await authenticatedFetch(streamUrl(jobId), {
        signal,
        headers: { Accept: "text/event-stream", ...(lastId ? { "Last-Event-ID": lastId } : {}) },
      });
      if (!response.ok) throw new ApiError(response.status, await response.text());
      if (!response.headers.get("content-type")?.includes("text/event-stream")) throw new Error("服务返回了无效的事件流。");
      onConnection(null);
      await readSse(response, event => {
        if (event.id) lastId = event.id;
        const result = onEvent(event);
        if (event.event === "eof" || result === false) { ended = true; return false; }
      }, signal);
      if (ended || signal.aborted) return;
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) throw error;
      if (attempt >= 4) throw error;
    }
    if (attempt >= 4) throw new Error("事件流多次中断，请刷新页面恢复任务。");
    onConnection("连接中断，正在恢复任务进度…");
    await new Promise<void>(resolve => {
      const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
      const timer = setTimeout(done, Math.min(1000 * 2 ** attempt, 8000));
      signal.addEventListener("abort", done, { once: true });
    });
  }
}
