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
