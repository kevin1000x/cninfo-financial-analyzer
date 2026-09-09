# finaudit payload — filled at deploy time, empty in this repository

This directory is a mount point, not source. It stays empty here on purpose.

`api/audit_mount.py` registers `/audit/coverage` and `/audit/answer` **only if**
the finaudit packages import from this path *and* `FINAUDIT_API_TOKEN` is set.
In a plain clone of this repository neither holds, so the app builds and runs
exactly as it did before — the two routes simply do not exist.

## Why not commit the source here

finaudit is a separate project with its own repository, tests and release
gates. Copying its source into this repo would create a second copy that
drifts, and there would be no answer to "which one is authoritative".

The Hugging Face Space is a *deploy snapshot* of both projects, not a
development checkout of either. Its history is deploy commits; that is the
existing convention here, and this change follows it rather than inventing a
second one.

## Why the two projects share one container at all

Hugging Face stopped issuing new Docker Spaces to free accounts in mid-2026.
This Space was created 2026-05-03 and predates that, so it is the only
container this account can run. Sharing it is cheaper than standing up a
second host, and the sharing is shallow: one container, two independent
request paths, separate tokens, no shared state, no shared dependencies.

finaudit adds **no** package to this image — its only third-party import is
PyYAML, which `requirements.txt` already pulls in — and its four top-level
packages (`agent`, `semantic_layer`, `service`, `extractor`) collide with
nothing this app imports.

## What the deploy snapshot puts here

```
agent/  semantic_layer/  service/  extractor/   the code
metrics/                                        metric definitions
data/extracted/                                 hand-verified annual-report figures
eval/frozen-01/fixtures/                        synthetic fixtures, labelled as such
```

Roughly 770 KB. No PDFs, no credentials — both tokens are Space secrets
(`FINAUDIT_API_TOKEN`, `FINAUDIT_MODEL_KEY`).
