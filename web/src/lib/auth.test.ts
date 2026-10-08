import { afterEach, expect, it, vi } from "vitest";
const { getSession } = vi.hoisted(() => ({ getSession: vi.fn() }));
vi.mock("@supabase/supabase-js", () => ({ createClient: () => ({ auth: { getSession } }) }));
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules(); getSession.mockReset(); });

it("returns email verification and recovery to the exact production root", async () => {
  const { AUTH_REDIRECT_URL } = await import("./auth");
  expect(AUTH_REDIRECT_URL).toBe("https://cninfo-analyzer-web.pages.dev/");
  const callback = new URL(AUTH_REDIRECT_URL);
  expect(callback.hash).toBe("");
  expect(callback.search).toBe("");
});

it("supplies only the current user's Bearer, never a token in the URL", async () => {
  vi.stubEnv("VITE_AUTH_MODE", "supabase"); vi.stubEnv("VITE_SUPABASE_URL", "https://example.supabase.co"); vi.stubEnv("VITE_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test");
  getSession.mockResolvedValue({ data: { session: { access_token: "user-session" } }, error: null });
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({})); vi.stubGlobal("fetch", fetcher);
  const { authenticatedFetch } = await import("./auth");
  await authenticatedFetch("/api/proxy/jobs/abc/result");
  expect(fetcher.mock.calls[0]?.[0]).toBe("/api/proxy/jobs/abc/result");
  const [, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(new Headers(init.headers).get("Authorization")).toBe("Bearer user-session");
});
it("fails closed when Supabase mode has no configuration", async () => {
  vi.stubEnv("VITE_AUTH_MODE", "supabase"); vi.stubEnv("VITE_SUPABASE_URL", ""); vi.stubEnv("VITE_SUPABASE_PUBLISHABLE_KEY", "");
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const { authenticatedFetch } = await import("./auth");
  await expect(authenticatedFetch("/api/proxy/jobs", { method: "POST" })).rejects.toThrow("尚未配置");
  expect(fetcher).not.toHaveBeenCalled();
});

it("does not send protected requests when configured but signed out", async () => {
  vi.stubEnv("VITE_AUTH_MODE", "supabase");
  vi.stubEnv("VITE_SUPABASE_URL", "https://example.supabase.co");
  vi.stubEnv("VITE_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test");
  getSession.mockResolvedValue({ data: { session: null }, error: null });
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { authenticatedFetch } = await import("./auth");
  await expect(authenticatedFetch("/api/audit/verify", { method: "POST" })).rejects.toThrow("请先登录");
  expect(fetcher).not.toHaveBeenCalled();
});
