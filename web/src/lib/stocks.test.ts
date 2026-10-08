import { afterEach, expect, it, vi } from "vitest";
import { parseStockCodes, searchStocks } from "./stocks";

afterEach(() => vi.unstubAllGlobals());
it("retains leading zeros, accepts Chinese separators and deduplicates", () => {
  expect(parseStockCodes("000001，600519；000001\n600309、bad")).toEqual({ valid: ["000001", "600519", "600309"], invalid: ["bad"] });
});
it("encodes name/pinyin queries and exposes catalogue outages", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ items: [], total: 0, updated_at: null, stale: true }));
  vi.stubGlobal("fetch", fetcher);
  await searchStocks(" 茅台 ");
  expect(fetcher.mock.calls[0]?.[0]).toBe("/api/proxy/stocks?q=%E8%8C%85%E5%8F%B0&limit=20");
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response("", { status: 503 })));
  await expect(searchStocks("600519")).rejects.toThrow("批量代码");
});
