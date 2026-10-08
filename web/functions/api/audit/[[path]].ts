// Same-origin proxy for the finaudit answering service.
//
// A SECOND backend, deliberately on its own route and its own secrets.
// Do not fold it into functions/api/proxy/[[path]].ts:
//
//   /api/proxy/*  → cninfo FastAPI. Stateful job runner, one job at a time,
//                   429 on concurrency, SSE streaming.
//   /api/audit/*  → finaudit. Stateless request/response. No jobs, no
//                   streaming, nothing shared between two requests.
//
// As of 2026-09-08 both happen to run in the same container: Hugging Face
// stopped issuing new Docker Spaces to free accounts, and that Space predates
// the change, so finaudit is mounted into it at /audit/*. That is a hosting
// accident, not a merge — separate tokens, separate failure modes, separate
// error handling below. Keeping the routes apart is what lets either one move
// to its own origin later by changing one variable instead of this file.
//
// Required Pages secrets (never commit them):
//   AUDIT_API_BASE        — origin (and path prefix) of the finaudit service,
//                           no trailing slash. Today:
//                           https://rgt07-cninfo-financial-analyzer.hf.space/audit
//   AUDIT_API_TOKEN       — the service's own token (FINAUDIT_API_TOKEN over there)
//   AUDIT_PLATFORM_TOKEN  — OPTIONAL. The hosting platform's own gate token.
//
// Why two tokens and two headers:
//
// The current Space is public, so AUDIT_PLATFORM_TOKEN stays unset. Legacy mode
// can use Authorization for a platform/service token; Supabase mode reserves
// that header for the current user and rejects a conflicting platform token.
//
// Service/platform tokens never reach the browser. In AUTH_MODE=supabase,
// Authorization carries the user session, X-Finaudit-Token carries the service
// credential, and AUDIT_PLATFORM_TOKEN must be absent to avoid overwriting user auth.

interface Env {
  AUTH_MODE?: "legacy" | "supabase";
  AUDIT_API_BASE: string;
  AUDIT_API_TOKEN: string;
  AUDIT_PLATFORM_TOKEN?: string;
}

interface PagesContext {
  request: Request;
  env: Env;
  params: { path: string[] };
}

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
]);

// Only what the service actually exposes. An allowlist rather than a
// pass-through: this proxy carries a bearer token, so anything it forwards is
// forwarded *as an authenticated caller*. A catch-all would hand that
// authority to any path someone can put in a URL.
const ALLOWED = new Set(["coverage", "answer", "verify"]);

export const onRequest = async (ctx: PagesContext): Promise<Response> => {
  const { request, env, params } = ctx;

  const mode = env.AUTH_MODE ?? "legacy";
  if (!["legacy", "supabase"].includes(mode) || (mode === "supabase" && env.AUDIT_PLATFORM_TOKEN)) {
    return json(500, { detail: "核查服务认证配置不一致。" });
  }
  if (!env.AUDIT_API_BASE || !env.AUDIT_API_TOKEN) {
    return json(500, {
      detail: "proxy misconfigured: AUDIT_API_BASE / AUDIT_API_TOKEN missing",
    });
  }

  const segments = params.path ?? [];
  if (segments.length !== 1 || !ALLOWED.has(segments[0])) {
    return json(404, {
      detail: "只有 GET /coverage、POST /verify 与 POST /answer",
    });
  }
  const method = segments[0] === "coverage" ? "GET" : "POST";
  if (request.method !== method) {
    return json(405, { detail: "只接受 GET 与 POST" });
  }

  const authorization = request.headers.get("Authorization");
  if (mode === "supabase" && !/^Bearer\s+\S+$/i.test(authorization ?? "")) {
    return json(401, { detail: "请先登录研究账户。" });
  }

  const url = new URL(request.url);
  const upstreamUrl = `${env.AUDIT_API_BASE.replace(/\/$/, "")}/${segments[0]}${url.search}`;

  const fwdHeaders = new Headers();
  for (const [k, v] of request.headers.entries()) {
    const key = k.toLowerCase();
    if (HOP_BY_HOP.has(key)) continue;
    // Strip anything a caller might use to impersonate the proxy's own auth.
    if (key === "host" || key === "cookie") continue;
    if (key === "authorization" || key === "x-finaudit-token") continue;
    fwdHeaders.set(k, v);
  }
  // Our own token always goes on our own header.
  fwdHeaders.set("X-Finaudit-Token", env.AUDIT_API_TOKEN);
  // Supabase mode preserves user auth; legacy mode retains the old platform gate.
  fwdHeaders.set(
    "Authorization",
    mode === "supabase" ? authorization! : `Bearer ${env.AUDIT_PLATFORM_TOKEN || env.AUDIT_API_TOKEN}`,
  );

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers: fwdHeaders,
      body: request.method === "GET" ? undefined : request.body,
      // @ts-expect-error — Cloudflare Workers fetch supports this option.
      duplex: "half",
    });
  } catch (err) {
    // The upstream being unreachable is a transport failure, and it must not
    // be dressed up as an answer. The page distinguishes "couldn't reach the
    // service" from "the service declined to answer"; that distinction only
    // survives if this layer keeps it too.
    return json(502, {
      detail: `连不上问答服务：${err instanceof Error ? err.message : String(err)}`,
    });
  }

  const respHeaders = new Headers();
  const ct = upstream.headers.get("content-type");
  respHeaders.set("Content-Type", ct ?? "application/json; charset=utf-8");
  // Answers are computed per request from versioned definitions; a cached one
  // could show a figure produced by a definition version the page no longer
  // names. Cheap to recompute, expensive to be wrong about.
  respHeaders.set("Cache-Control", "no-store");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  });
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
