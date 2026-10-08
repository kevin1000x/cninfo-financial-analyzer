# CNINFO workbench integration draft

This branch combines the CNINFO API and React web application while keeping
FinAudit's evaluation and source authority in its original repository.
The backend remains at repository root; the frontend lives in `web/`.

## Source versions

`release-lock.json` records the source commits. The frontend was imported using
`git subtree add --prefix web ... --squash`, preserving its upstream snapshot
identity. Backend Git history remains intact. No remote repository was deleted,
renamed, or made private/public by this integration.

FinAudit is not copied from the developer's working tree. Prepare its runtime
from the pinned Git objects:

```sh
git clone https://github.com/kevin1000x/finaudit-agent.git .finaudit-source
python scripts/prepare_finaudit.py --source .finaudit-source
docker build -t cninfo-workbench .
```

The preparation command refuses an existing output directory. For another
reviewed build use a new `--output` path; do not point it at a project directory.
The runtime includes only upstream Docker COPY targets plus LICENSE. A manifest
records the SHA-256 of each exported file. Frozen evaluation files retain their
original bytes; workshop records and raw PDFs are excluded.

## Deployment layout

Cloudflare Pages continues to serve the frontend, and the existing HF Space
continues to host the API. A monorepo does not require moving either host.
The canonical website is **https://cninfo-analyzer-web.pages.dev/**; the API is
**https://rgt07-cninfo-financial-analyzer.hf.space**. Local previews are optional
development tools and are not the production login destination.

| Setting | Value |
| --- | --- |
| Pages root directory | `web` |
| Pages build command | `npm run build` |
| Pages output directory | `dist` |
| Backend Docker build context | repository root |
| Backend HTTP port | `7860` |
| Deployment readiness probe | `/readyz` must return 200 |

The root GitHub workflow runs frontend checks, backend checks, and a pinned
FinAudit runtime/image build. `web/.github/workflows/` is retained as upstream
history context; GitHub only discovers workflows in the repository root.

## Supabase account configuration

Create an Auth project in the selected organization. The frontend only receives
the project URL and publishable key. Never give it a service-role/secret key.

Frontend build variables:

```dotenv
VITE_AUTH_MODE=supabase
VITE_SUPABASE_URL=https://PROJECT_REF.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_REPLACE_ME
```

Backend runtime variables:

```dotenv
AUTH_MODE=supabase
SUPABASE_URL=https://PROJECT_REF.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_REPLACE_ME
REQUIRE_AUDIT=1
FINAUDIT_API_TOKEN=SERVER_SIDE_ONLY
```

Pages Functions runtime variables:

```dotenv
AUTH_MODE=supabase
API_BASE=https://rgt07-cninfo-financial-analyzer.hf.space
AUDIT_API_BASE=https://rgt07-cninfo-financial-analyzer.hf.space/audit
AUDIT_API_TOKEN=SERVER_SIDE_ONLY
```

`AUDIT_API_TOKEN` must match the backend's `FINAUDIT_API_TOKEN`. Leave
`AUDIT_PLATFORM_TOKEN` unset for this public HF Space. The user Authorization
header must reach the API; a shared server token must not replace it.

In Supabase Auth URL Configuration, set **Site URL** to
`https://cninfo-analyzer-web.pages.dev/` and set the **Redirect URLs** allowlist to
that same exact URL. Do not add localhost, preview wildcards, or `#/audit` for
the production project. Both registration confirmation and password recovery
use this root URL explicitly. Supabase processes callback tokens with
`detectSessionInUrl` before the app is shown; the app must not rewrite the
callback fragment to a view route. After `PASSWORD_RECOVERY`, users set their
new password through `updateUser`.

Enable email/password sign-up, keep email verification enabled, and configure
production SMTP before opening registration to the public. Do not disable
verification to make a smoke test pass. Production acceptance requires the
new frontend and API deployed with these settings, actual confirmation/recovery
emails returning to the formal site, and a live two-account isolation check.
A configured Supabase project or successful local build alone does not mean
the production site has been integrated or these live flows have passed.

Supabase Auth manages account records. No custom profile/history table is needed
to authenticate a user. Jobs in this iteration remain process-local and are
bound to the authenticated user's id. Durable history and synced watchlists are
separate future additions, requiring owner-scoped RLS and retention settings.

### Optional local development

Local Vite/FastAPI settings are documented separately in `web/README.md` and
commented in `web/.env.example`. They support code checks and API development;
they do not change the production Auth Site URL or callback allowlist. This
build always sends auth emails back to the formal site, including when opened
from a local preview. Use the deployed site for email-flow acceptance.

## Authorization and migration

`AUTH_MODE=legacy` keeps existing local behavior for compatibility. A production
account deployment must explicitly set `supabase` on both API and Pages.
Missing Supabase configuration fails closed. The backend validates access tokens
with the Auth service, then checks task ownership for status, SSE, download, and
cancellation. Busy responses do not reveal another user's task id. Audit calls
also require the user session and the existing internal FinAudit token.

An in-progress legacy job has no authenticated owner. Complete or cancel it
before switching modes; restarting also clears all process-local jobs. Switch
the API and Pages configuration together. Test anonymous rejection, account A/B
isolation, token expiry, SSE reconnect, authenticated download, and logout before
routing production users to the new branch.

## Known scope and release blockers

- Stock search uses CNINFO's actual code/name/pinyin fields and cached directory.
  Search coverage and financial-verification coverage are different. Unsupported
  markets must be marked; they must not silently be sent to a different market.
- FinAudit remains pinned to its existing two verified company-year datasets.
  Its prior correctness/evaluation findings are documented in the review; this
  integration does not fix or revalidate its research claims.
- There is still one global CPU task slot. User isolation does not add a queue or
  per-user rate limit. Add quotas/rate limits before public scale-up.
- Deployments, remote repository changes, and a live two-account login exercise
  are not implied by a successful local build.
