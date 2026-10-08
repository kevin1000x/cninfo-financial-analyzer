# cninfo-analyzer-web

Web frontend for the [cninfo-financial-analyzer](https://github.com/kevin1000x/cninfo-financial-analyzer) Python pipeline. Lets users submit a stock-code/year batch, watch the streaming-analysis log, and download the resulting xlsx — all without touching the CLI.

> **Note:** Result rows may be fewer than submitted tasks when CNINFO has no matching announcement for a stock/year/type combination, or a report fails to download or parse. This is pipeline-layer behavior, not a web-layer bug.

## Stack

- **Vite 8** + React 19 + TypeScript 6
- **Tailwind CSS v4** (no PostCSS step, via `@tailwindcss/vite`)
- **Fetch streaming** for SSE with the current user’s Bearer token, including reconnects
- **Cloudflare Pages Functions** (`functions/api/proxy/[[path]].ts`) for the same-origin proxy in production
- **Vite `server.proxy`** for the same proxy in local dev

There is intentionally no state management library, no custom routing, and no UI framework beyond Tailwind. Adding shadcn/ui later is `npx shadcn@latest init` away.

UI design decisions — tokens, typography, motion, states — live in [DESIGN.md](./DESIGN.md); read it before changing the interface.

## Production deployment

The canonical website is **https://cninfo-analyzer-web.pages.dev/**. The existing
API is **https://rgt07-cninfo-financial-analyzer.hf.space**. See the root
[INTEGRATION.md](../INTEGRATION.md) for the pinned FinAudit runtime and release checks.

Cloudflare Pages uses repository root directory `web`, build command
`npm run build`, and output directory `dist`. Set these build-time variables:

```dotenv
VITE_AUTH_MODE=supabase
VITE_SUPABASE_URL=https://PROJECT_REF.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_REPLACE_ME
```

Set these Pages Functions runtime variables in the dashboard:

```dotenv
AUTH_MODE=supabase
API_BASE=https://rgt07-cninfo-financial-analyzer.hf.space
AUDIT_API_BASE=https://rgt07-cninfo-financial-analyzer.hf.space/audit
AUDIT_API_TOKEN=SERVER_SIDE_ONLY
```

The backend must also use `AUTH_MODE=supabase` with the same Supabase project;
`AUDIT_API_TOKEN` must match its server-only `FINAUDIT_API_TOKEN`. Leave
`AUDIT_PLATFORM_TOKEN` unset for the existing public HF Space. Both proxies
preserve the user's Bearer token. Audit additionally sends `X-Finaudit-Token`;
no server credential belongs in a `VITE_` variable. SSE and downloads use
Bearer-authenticated fetch, without putting tokens in URLs.

In Supabase Auth URL Configuration, use **https://cninfo-analyzer-web.pages.dev/**
for both **Site URL** and the sole exact **Redirect URL**. Do not add localhost,
preview wildcards, or `#/audit` to this production project. Registration and
recovery emails explicitly return to this root URL. Keep email verification
enabled and configure production SMTP before opening registration publicly.

After return, Supabase consumes the callback fragment before the workbench is
shown. The app reads view hashes without rewriting them. A `PASSWORD_RECOVERY`
event opens the new-password form, which calls `updateUser`.

The production frontend and API must be deployed together with their new auth
settings before this account flow is available on the formal site. Local
configuration, passing tests, and a healthy Supabase project do not establish
production integration. Verify real confirmation/recovery emails and account
A/B isolation on the formal site after deployment.

## Optional local development

This is for code checks and development only. It does not replace the formal
site or change its Supabase Site URL/redirect allowlist. Email callbacks from
this build still return to the formal site.

```bash
# Terminal 1 — repository root; configure backend Supabase env separately
export AUTH_MODE=supabase
export FINAUDIT_API_TOKEN=local-preview-only  # local example only
export CNINFO_COOKIES_FILE=/path/to/cookies.json
uvicorn api.main:app --reload --port 8000

# Terminal 2 — web/; copy .env.example to .env.local and set public auth values
cd web
npm run dev
```

Optional web `.env.local` settings:

```dotenv
DEV_API_BASE=http://127.0.0.1:8000
FINAUDIT_API_TOKEN=local-preview-only
```

The Vite server forwards `/api/proxy/*` to `/*` and `/api/audit/*` to `/audit/*`
on the same backend (port 8000 by default). It adds the local FinAudit service
header without replacing the user's Bearer. The backend still needs its pinned
FinAudit runtime and matching auth configuration. There is no default standalone
port 8100 service. Production secrets must not be copied into these examples.

## 结论核查（`#/audit`）

第二个视图，接的是同一 API 中独立的 `/audit` 路由，核查引擎来自固定版本的 FinAudit。

把一段财报分析结论粘进来（券商 AI、问财、雪球、任何 LLM 的输出），系统把它拆成
可核声明**逐条判定**，每条附一条可独立复核的证据链。
**一个输入框**：不带数就是提问，带数就是待核声明，系统自己判并把判定印在第一行。

⚠️ URL 仍是 `#/audit`（那个链接已经在流通），改的是标签与内容，不是地址。它与批量分析使用不同路由、独立的核查服务凭据和不同的请求形态；用户登录鉴权共用：

| | `/api/proxy/*` | `/api/audit/*` |
|---|---|---|
| 后端 | cninfo FastAPI | 同一 FastAPI 的 `/audit` + FinAudit 引擎 |
| 形态 | 有状态任务，单任务，并发 429，SSE 流 | **无状态请求–响应**，无任务、无流 |
| 用途 | 提交一批年报，看日志，下 xlsx | 粘一段结论，逐条判定 + 每条一条可复核的证据链 |

设计意图与**五条**硬约束（覆盖面先于输入框、真实/合成必须分得开、正文逐字来自服务端、
第一行印「我把这段话读成什么」、**「核不了」不许漆成红叉**）
写在 `DESIGN.md` 的「第二个视图」一节。

`/api/audit/*` 只放行 `GET coverage`、`POST answer` 和 `POST verify`。
Supabase 模式要求当前用户登录，并向后端保留其 Bearer；后端同时校验内部核查令牌。
核查公司/年份的覆盖范围独立于全市场公司搜索目录。配置和部署步骤见上方。

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
│   ├── StreamView.tsx  # authenticated fetch stream + log rendering
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

- **自动获取 AKShare 财务数据**（默认）：后端获取年度 ROA，且在配置数据缓存后缓存指标；财务数据缓存由后端访问。浏览器仅为用户鉴权访问 Supabase Auth。
- **使用 examples/financial_data.csv**：用于可复现的演示数据。
- **不计算 TNI**：仅运行情感与可读性分析。

## What's intentionally not implemented (yet)

- **Custom financial-data upload** — AKShare 与示例 CSV 已支持，用户上传仍需独立的格式校验与访问控制
- **Multiple concurrent jobs** — backend is single-job by design (returns 429); UI matches
- **Charts** — once the xlsx workflow is solid, add an inline `tone_normalized` × year scatter
- **shadcn/ui polish** — manual Tailwind for v0; promote to shadcn when component reuse warrants it


## Company search and account mode (2026-10-09)

Stock search calls `GET /api/proxy/stocks?q=&limit=20`; the backend provides names, codes and source pinyin. Batch pasting remains available. Directory membership does not imply FinAudit verified coverage.

Email/password registration, login, logout, and password recovery use Supabase
Auth. Missing browser configuration disables registration/login, and Supabase
mode disables job submission until a session exists. The browser accepts only
an `sb_publishable_` key. Refer to the production setup above for exact domains,
callback settings, and the checks still required on the deployed site.

Active job ids are scoped to the signed-in user in localStorage. A signed-out
user cannot restore a private job. This version does not claim durable task
history: backend job lifecycle/storage limits still apply.

Existing deployments may explicitly retain `AUTH_MODE=legacy` with their
server-only `API_TOKEN`; that compatibility mode does not provide per-user task
isolation. Switch browser build mode, Pages mode, and backend mode together.

Validation: `npm run lint`, `npm test`, `npm run build`.
