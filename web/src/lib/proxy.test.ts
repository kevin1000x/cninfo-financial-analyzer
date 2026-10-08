import { afterEach, expect, it, vi } from "vitest";
import { onRequest } from "../../functions/api/proxy/[[path]]";
afterEach(() => vi.unstubAllGlobals());
const base = { API_BASE: "https://backend.example", AUTH_MODE: "supabase" as const };

it("never lends the legacy token to arbitrary upstream routes", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const response = await onRequest({ request: new Request("https://web.example/api/proxy/audit/verify"), env: { ...base, AUTH_MODE: "legacy", API_TOKEN: "service-secret" }, params: { path: ["audit", "verify"] } });
  expect(response.status).toBe(404); expect(fetcher).not.toHaveBeenCalled();
});
it("rejects anonymous job requests in Supabase mode", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const response = await onRequest({ request: new Request("https://web.example/api/proxy/jobs", { method: "POST" }), env: base, params: { path: ["jobs"] } });
  expect(response.status).toBe(401); expect(fetcher).not.toHaveBeenCalled();
});
it("forwards user Bearer and resume id without cookies or internal token headers", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => new Response("data: {}\n\n", { headers: { "Content-Type": "text/event-stream" } })); vi.stubGlobal("fetch", fetcher);
  const response = await onRequest({ request: new Request("https://web.example/api/proxy/jobs/abc/stream", { headers: { Authorization: "Bearer user-session", Cookie: "secret", "X-Finaudit-Token": "spoof", "Last-Event-ID": "4" } }), env: base, params: { path: ["jobs", "abc", "stream"] } });
  expect(response.status).toBe(200);
  const [, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  const headers = new Headers(init.headers);
  expect(headers.get("Authorization")).toBe("Bearer user-session"); expect(headers.get("Last-Event-ID")).toBe("4"); expect(headers.has("cookie")).toBe(false); expect(headers.has("x-finaudit-token")).toBe(false);
  expect(await response.text()).toBe("data: {}\n\n");
});
it("keeps the stock directory public and enforces exact methods", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => Response.json({ items: [] })));
  expect((await onRequest({ request: new Request("https://web.example/api/proxy/stocks"), env: base, params: { path: ["stocks"] } })).status).toBe(200);
  expect((await onRequest({ request: new Request("https://web.example/api/proxy/jobs/abc/cancel"), env: base, params: { path: ["jobs", "abc", "cancel"] } })).status).toBe(405);
});

import { onRequest as auditProxy } from "../../functions/api/audit/[[path]]";
it("audit keeps service token separate while forwarding user authentication", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({})); vi.stubGlobal("fetch", fetcher);
  const response = await auditProxy({ request: new Request("https://web.example/api/audit/verify", { method: "POST", headers: { Authorization: "Bearer user-session", "X-Finaudit-Token": "spoofed" }, body: '{"text":"test"}' }), env: { AUTH_MODE: "supabase", AUDIT_API_BASE: "https://backend.example/audit", AUDIT_API_TOKEN: "service-secret" }, params: { path: ["verify"] } });
  expect(response.status).toBe(200);
  const [, init] = fetcher.mock.calls[0]!;
  const headers = new Headers(init?.headers);
  expect(headers.get("Authorization")).toBe("Bearer user-session"); expect(headers.get("X-Finaudit-Token")).toBe("service-secret");
});
it("audit denies anonymous and platform-token replacement in Supabase mode", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const context = { request: new Request("https://web.example/api/audit/coverage"), env: { AUTH_MODE: "supabase" as const, AUDIT_API_BASE: "https://backend.example/audit", AUDIT_API_TOKEN: "service-secret" }, params: { path: ["coverage"] } };
  expect((await auditProxy(context)).status).toBe(401);
  expect((await auditProxy({ ...context, env: { ...context.env, AUDIT_PLATFORM_TOKEN: "platform" } })).status).toBe(500);
  expect(fetcher).not.toHaveBeenCalled();
});
