# cninfo-analyzer-web

Web frontend for the [cninfo-financial-analyzer](https://github.com/kevin1000x/cninfo-financial-analyzer) Python pipeline. Lets users submit a stock-code/year batch, watch the streaming-analysis log, and download the resulting xlsx — all without touching the CLI.

> **Note:** Result rows may be fewer than submitted tasks when CNINFO has no matching announcement for a stock/year/type combination, or a report fails to download or parse. This is pipeline-layer behavior, not a web-layer bug.

## Stack

- **Vite 8** + React 19 + TypeScript 6
- **Tailwind CSS v4** (no PostCSS step, via `@tailwindcss/vite`)
- **EventSource** native API for streaming (no third-party SSE lib needed because the backend is reached through a same-origin proxy)
- **Cloudflare Pages Functions** (`functions/api/proxy/[[path]].ts`) for the same-origin proxy in production
- **Vite `server.proxy`** for the same proxy in local dev

There is intentionally no state management library, no custom routing, and no UI framework beyond Tailwind. Adding shadcn/ui later is `npx shadcn@latest init` away.

UI design decisions — tokens, typography, motion, states — live in [DESIGN.md](./DESIGN.md); read it before changing the interface.

## Local development

The frontend talks to a locally-running FastAPI backend through Vite's dev-server proxy. Start both:

```bash
# Terminal 1 — backend
cd /path/to/cninfo-financial-analyzer
export CNINFO_COOKIES_FILE=/path/to/cookies.json   # JSON jar from a logged-in browser
uvicorn api.main:app --reload --port 8000

# Terminal 2 — frontend
cd /path/to/cninfo-analyzer-web
npm run dev
# open http://localhost:5173
```

The backend should run **without** `API_TOKEN` locally; the dev proxy doesn't inject Authorization. Any request the browser makes to `/api/proxy/*` is rewritten by Vite to `http://localhost:8000/*` and the response (including SSE) streams straight back.

## Production deployment

`functions/api/proxy/[[path]].ts` is a Cloudflare Pages Function that takes the same `/api/proxy/*` URL and:

1. Forwards to a named Cloudflare Tunnel that points at the FastAPI backend
2. Injects `Authorization: Bearer <API_TOKEN>` from a Pages secret
3. Streams `/jobs/{id}/stream` responses with `ReadableStream` pass-through

Two Pages secrets to set before `wrangler pages deploy`:

```bash
npx wrangler pages secret put API_BASE   # https://cninfo-api.your-domain.com
npx wrangler pages secret put API_TOKEN  # same value uvicorn was started with
```

`API_BASE` must be a **named** tunnel; quick tunnels (`*.trycloudflare.com`) don't support SSE.

## 结论核查（`#/audit`）

第二个视图，接的是 **另一个后端**：finaudit 的核查服务。

把一段财报分析结论粘进来（券商 AI、问财、雪球、任何 LLM 的输出），系统把它拆成
可核声明**逐条判定**，每条附一条可独立复核的证据链。
**一个输入框**：不带数就是提问，带数就是待核声明，系统自己判并把判定印在第一行。

⚠️ URL 仍是 `#/audit`（那个链接已经在流通），改的是标签与内容，不是地址。它和批量分析链路不共用任何东西 ——
不同的路由、不同的 Pages secret、不同的形态：

| | `/api/proxy/*` | `/api/audit/*` |
|---|---|---|
| 后端 | cninfo FastAPI | finaudit 问答服务 |
| 形态 | 有状态任务，单任务，并发 429，SSE 流 | **无状态请求–响应**，无任务、无流 |
| 用途 | 提交一批年报，看日志，下 xlsx | 粘一段结论，逐条判定 + 每条一条可复核的证据链 |

设计意图与**五条**硬约束（覆盖面先于输入框、真实/合成必须分得开、正文逐字来自服务端、
第一行印「我把这段话读成什么」、**「核不了」不许漆成红叉**）
写在 `DESIGN.md` 的「第二个视图」一节。

### 本地开发

```bash
# 终端 1 —— finaudit 问答服务（无状态，零 Web 框架依赖）
cd /path/to/finaudit-agent
PYTHONPATH=src python -m service.api      # 监听 8100

# 终端 2 —— 前端
npm run dev                                # http://localhost:5173/#/audit
```

`vite.config.ts` 把 `/api/audit/*` 转发到 `http://localhost:8100`。
问答服务绑回环地址时不需要令牌；**绑非回环地址且没设令牌会拒绝启动**（有意如此：
「设了才检查」会让「部署时忘了配」变成一个完全敞开、且没有任何一处会响的服务）。

### 生产部署：多两个 Pages secret

```bash
npx wrangler pages secret put AUDIT_API_BASE        # 问答服务的 origin，不带尾斜杠
npx wrangler pages secret put AUDIT_API_TOKEN       # = 服务端的 FINAUDIT_API_TOKEN
npx wrangler pages secret put AUDIT_PLATFORM_TOKEN  # 可选：托管平台自己的门禁令牌
```

`AUDIT_API_TOKEN` 与服务端的 `FINAUDIT_API_TOKEN` 必须是同一个值（两边名字不同）。

**为什么会有两个令牌、两个头**：问答服务跑在一个 **private** 的 Hugging Face Space 上，
平台自己的门禁吃 `Authorization: Bearer <hf_token>`。两个令牌塞不进一个头，所以
服务自己的令牌走 `X-Finaudit-Token`，`Authorization` 留给平台。
没配 `AUDIT_PLATFORM_TOKEN` 时（public Space、或没有自带门禁的托管），
两个头都发我们自己的令牌 —— 同一份 Function 两种情形都能用。

`functions/api/audit/[[path]].ts` 只放行 `coverage` 与 `answer` 两条路径 ——
它带着 bearer token 转发，等于以「已认证调用方」的身份出去，
catch-all 会把这份权限交给任何人能写进 URL 的路径。

未配置这两个 secret 时该页显示「没能问到服务」，**不影响批量分析那一页**。

## Layout

```
src/
├── lib/
│   ├── api.ts          # fetch wrappers around /api/proxy/...
│   ├── jobStore.ts     # localStorage for the active job id
│   ├── types.ts        # mirrors backend SSE payload shapes
│   └── utils.ts        # cn() helper
├── components/
│   ├── JobForm.tsx     # stock codes / years / report type / TNI toggle
│   ├── StreamView.tsx  # EventSource consumer + log rendering
│   └── ResultCard.tsx  # done state + xlsx download
├── App.tsx             # boot, refresh-recovery, view orchestration
├── main.tsx
├── index.css           # Tailwind v4 entry
functions/api/proxy/
└── [[path]].ts         # Cloudflare Pages Function (prod-only)
```

## Refresh recovery

The active `job_id` is mirrored to `localStorage`. On page load the app calls `GET /api/proxy/jobs/{id}` first; if the backend still knows about it, the SSE stream is reattached and the user sees the full history (capped at 1000 events on the backend) replayed as if they never left.

If the backend has restarted (in-memory job state is lost), the stale id is silently dropped and the form is shown.

## TNI financial-data choices

- **自动获取 AKShare 财务数据**（默认）：后端获取年度 ROA，且在配置 Supabase 后缓存指标；浏览器不会访问 Supabase。
- **使用 examples/financial_data.csv**：用于可复现的演示数据。
- **不计算 TNI**：仅运行情感与可读性分析。

## What's intentionally not implemented (yet)

- **Custom financial-data upload** — AKShare 与示例 CSV 已支持，用户上传仍需独立的格式校验与访问控制
- **Multiple concurrent jobs** — backend is single-job by design (returns 429); UI matches
- **Auth UI** — token lives in the Pages Function, the browser never sees it
- **Charts** — once the xlsx workflow is solid, add an inline `tone_normalized` × year scatter
- **shadcn/ui polish** — manual Tailwind for v0; promote to shadcn when component reuse warrants it
