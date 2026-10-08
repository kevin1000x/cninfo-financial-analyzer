import { describe, expect, it } from "vitest";
import { advanceEventId } from "./sse";

describe("advanceEventId", () => {
  it("advances the watermark for a new id", () => {
    expect(advanceEventId("1", 0)).toBe(1);
    expect(advanceEventId("7", 3)).toBe(7);
  });

  it("drops a replayed id at or below the watermark", () => {
    expect(advanceEventId("3", 3)).toBeNull();
    expect(advanceEventId("2", 5)).toBeNull();
  });

  it("applies events with no usable id and leaves the watermark alone", () => {
    // A proxy that strips `id:`, or a locally synthesized event. Dropping
    // these would silently swallow the stream.
    expect(advanceEventId("", 4)).toBe(4);
    expect(advanceEventId("not-a-number", 4)).toBe(4);
    expect(advanceEventId("0", 4)).toBe(4);
    expect(advanceEventId("-1", 4)).toBe(4);
  });

  it("lets only the new tail through a reconnect replay", () => {
    let watermark = 0;
    const applied: number[] = [];
    const deliver = (ids: string[]) => {
      for (const id of ids) {
        const next = advanceEventId(id, watermark);
        if (next === null) continue;
        watermark = next;
        applied.push(Number(id));
      }
    };

    deliver(["1", "2", "3", "4", "5"]);
    // EventSource auto-reconnects, replays the full history, then continues.
    deliver(["1", "2", "3", "4", "5", "6", "7"]);

    expect(applied).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });
});

import { readSse } from "./sse";

it("reads chunked CRLF/UTF-8, ignores heartbeat and preserves multiline data", async () => {
  const bytes = new TextEncoder().encode(': heartbeat\r\nid: 7\r\nevent: log\r\ndata: {"message":\r\ndata: "茅台"}\r\n\r\nevent: eof\r\ndata: {}\r\n\r\n');
  const response = new Response(new ReadableStream({ start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); controller.close(); } }));
  const events: unknown[] = [];
  await readSse(response, e => { events.push(e); });
  expect(events).toEqual([{ event: "log", id: "7", data: '{"message":\n"茅台"}' }, { event: "eof", id: "7", data: "{}" }]);
});
it("stops reading after eof without dispatching later events", async () => {
  const response = new Response('event: eof\ndata: {}\n\nevent: log\ndata: leaked\n\n');
  const events: string[] = [];
  await readSse(response, e => { events.push(e.event); return false; });
  expect(events).toEqual(["eof"]);
});
