/** Same-origin API boundary. AUTH_MODE must match the backend deployment. */
interface Env {
  API_BASE: string;
  AUTH_MODE?: "legacy" | "supabase";
  API_TOKEN?: string;
}
interface PagesContext { request: Request; env: Env; params: { path: string[] } }

function json(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: { "Cache-Control": "no-store" } });
}

export const onRequest = async ({ request, env, params }: PagesContext): Promise<Response> => {
  const mode = env.AUTH_MODE ?? "legacy";
  if (!env.API_BASE || !["legacy", "supabase"].includes(mode) || (mode === "legacy" && !env.API_TOKEN)) {
    return json(500, "分析服务尚未配置完成。");
  }
  const segments = params.path ?? [];
  const path = segments.join("/");
  // Never attach privileged credentials to arbitrary upstream paths.
  const isPublic = path === "stocks" || path === "healthz";
  const allowedMethod = isPublic ? "GET"
    : path === "jobs" ? "POST"
    : /^jobs\/[A-Za-z0-9_-]+\/cancel$/.test(path) ? "POST"
    : /^jobs\/[A-Za-z0-9_-]+(?:\/(?:stream|result))?$/.test(path) ? "GET"
    : null;
  if (!allowedMethod) return json(404, "没有这个接口。");
  if (request.method !== allowedMethod) return json(405, "不支持此请求方式。");

  const authorization = request.headers.get("Authorization");
  if (mode === "supabase" && !isPublic && !/^Bearer\s+\S+$/i.test(authorization ?? "")) {
    return json(401, "请先登录研究账户。");
  }
  const headers = new Headers();
  for (const name of ["Content-Type", "Accept", "Last-Event-ID"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (mode === "legacy") headers.set("Authorization", `Bearer ${env.API_TOKEN}`);
  else if (authorization) headers.set("Authorization", authorization);

  let upstream: Response;
  try {
    upstream = await fetch(`${env.API_BASE.replace(/\/$/, "")}/${path}${new URL(request.url).search}`, {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : request.body,
      // @ts-expect-error Cloudflare Workers supports streamed request bodies.
      duplex: "half",
    });
  } catch {
    return json(502, "暂时无法连接分析服务，请稍后重试。");
  }
  const responseHeaders = new Headers({ "Cache-Control": "no-store" });
  for (const name of ["Content-Type", "Content-Disposition", "Retry-After"]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (upstream.headers.get("Content-Type")?.includes("text/event-stream")) {
    responseHeaders.set("Cache-Control", "no-cache, no-store");
    responseHeaders.set("X-Accel-Buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
};
