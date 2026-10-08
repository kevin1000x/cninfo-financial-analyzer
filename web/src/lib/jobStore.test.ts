import { afterEach, expect, it, vi } from "vitest";
import { loadActiveJobId, saveActiveJobId } from "./jobStore";
afterEach(() => vi.unstubAllGlobals());
it("isolates accounts while preserving the legacy active-task key", () => {
  const storage = new Map<string, string>([["cninfo:active-job-id", "old-job"]]);
  vi.stubGlobal("window", { localStorage: { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) } });
  expect(loadActiveJobId()).toBe("old-job");
  saveActiveJobId("alice-job", "alice");
  expect(loadActiveJobId("bob")).toBeNull();
  expect(loadActiveJobId("alice")).toBe("alice-job");
  saveActiveJobId(null, "alice");
  expect(loadActiveJobId("alice")).toBeNull();
  expect(loadActiveJobId()).toBe("old-job");
});
