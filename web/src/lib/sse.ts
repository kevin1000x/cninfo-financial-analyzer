/**
 * SSE ids are job-global and monotonic (stamped by `api.runner._publish`).
 * EventSource replays the entire history whenever it auto-reconnects, and it
 * exposes the id of the event it just delivered as `MessageEvent.lastEventId`.
 * Dropping anything at or below the last applied id is what keeps a reconnect
 * from duplicating the whole log in the UI.
 */
export function advanceEventId(
  rawId: string,
  lastAppliedId: number,
): number | null {
  const id = Number(rawId);
  // No usable id — a proxy stripped it, or the event was synthesized. Apply
  // the event and leave the watermark alone rather than dropping everything.
  if (!Number.isFinite(id) || id <= 0) return lastAppliedId;
  if (id <= lastAppliedId) return null;
  return id;
}

export interface StreamEvent { event: string; data: string; id: string }

/** Incremental SSE parser: CRLF, comments, split UTF-8, named and multiline events. */
export async function readSse(response: Response, onEvent: (event: StreamEvent) => boolean | void, signal?: AbortSignal): Promise<void> {
  if (!response.body) throw new Error("服务没有返回事件流。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "message";
  let id = "";
  let data: string[] = [];
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    while (!signal?.aborted) {
      const chunk = await reader.read();
      if (signal?.aborted) return;
      buffer += decoder.decode(chunk.value, { stream: !chunk.done });
      if (buffer.length > 1_000_000) throw new Error("事件流消息超过大小限制。");
      let match: RegExpExecArray | null;
      while ((match = /\r\n|\r(?!$)|\n/.exec(buffer))) {
        const line = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        if (!line) {
          if (data.length && onEvent({ event, id, data: data.join("\n") }) === false) return;
          event = "message"; data = [];
          continue;
        }
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = value;
        else if (field === "data") data.push(value);
        else if (field === "id" && !value.includes("\0")) id = value;
      }
      if (chunk.done) return;
    }
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
