// Same-origin proxy for the cninfo-analyzer-web frontend.
//
// Drop this file at: cninfo-analyzer-web/functions/api/proxy/[[path]].ts
//
// Cloudflare Pages Functions auto-mounts every file under functions/ as a
// route. The [[path]] segment is a catch-all; e.g. a browser request to
//   GET /api/proxy/jobs/abc/stream
// reaches this handler with params.path = ["jobs", "abc", "stream"].
//
// Required Pages secrets (set via `wrangler pages secret put` or the
// dashboard, never commit them):
//   API_BASE   — the named-tunnel URL of the FastAPI backend, e.g.
//                "https://cninfo-api.example.com" (no trailing slash)
//   API_TOKEN  — matches what uvicorn was started with
//
// Behaviour:
//   * preserves method, body, query string
//   * for /jobs/{id}/stream returns upstream.body directly (ReadableStream
//     pass-through; do NOT await response.text())
//   * injects Authorization: Bearer <API_TOKEN>
//   * sets only the response headers that matter; hop-by-hop headers
//     never leak through
//
// The token only ever exists server-side; the browser never sees it.

interface Env {
  API_BASE: string;
  API_TOKEN: string;
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

export const onRequest = async (ctx: PagesContext): Promise<Response> => {
  const { request, env, params } = ctx;

  if (!env.API_BASE || !env.API_TOKEN) {
    return new Response("proxy misconfigured: API_BASE / API_TOKEN missing", {
      status: 500,
    });
  }

  const upstreamPath = (params.path ?? []).join("/");
  const url = new URL(request.url);
  const upstreamUrl = `${env.API_BASE.replace(/\/$/, "")}/${upstreamPath}${url.search}`;

  // Forward headers minus anything risky. Authorization is replaced.
  const fwdHeaders = new Headers();
  for (const [k, v] of request.headers.entries()) {
    const key = k.toLowerCase();
    if (HOP_BY_HOP.has(key)) continue;
    if (key === "host" || key === "authorization" || key === "cookie") continue;
    fwdHeaders.set(k, v);
  }
  fwdHeaders.set("Authorization", `Bearer ${env.API_TOKEN}`);

  const upstream = await fetch(upstreamUrl, {
    method: request.method,
    headers: fwdHeaders,
    body:
      request.method === "GET" || request.method === "HEAD"
        ? undefined
        : request.body,
    // @ts-expect-error — Cloudflare Workers fetch supports this option.
    duplex: "half",
  });

  // SSE: stream upstream.body straight back. Don't buffer.
  const isStream =
    upstreamPath.endsWith("/stream") ||
    upstream.headers.get("content-type")?.includes("text/event-stream");

  const respHeaders = new Headers();
  if (isStream) {
    respHeaders.set("Content-Type", "text/event-stream; charset=utf-8");
    respHeaders.set("Cache-Control", "no-cache");
    respHeaders.set("Connection", "keep-alive");
    respHeaders.set("X-Accel-Buffering", "no"); // disable nginx-style buffering if any
  } else {
    const ct = upstream.headers.get("content-type");
    if (ct) respHeaders.set("Content-Type", ct);
    const cd = upstream.headers.get("content-disposition");
    if (cd) respHeaders.set("Content-Disposition", cd);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  });
};
