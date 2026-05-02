#!/usr/bin/env bash
# Real-CNINFO smoke runner. Exercises the live HTTP API against the
# real downstream pipeline. Pre-conditions:
#   1. uvicorn api.main:app already running (default port 8000)
#   2. (optional) CNINFO_COOKIES_FILE pointing at a JSON cookie jar
#      exported from a logged-in browser, e.g.
#        {"JSESSIONID": "...", "JSESSIONID_BHLXR": "..."}
#   3. (optional) API_TOKEN matching what the API was started with
#
# Usage:
#   ./scripts/smoke_real.sh <stock_code> <year> [financial_data_csv]
#
# Examples:
#   # TNI happy path (600000 is in examples/financial_data.csv)
#   ./scripts/smoke_real.sh 600000 2022 examples/financial_data.csv
#
#   # Skip-TNI path (real stock, no matching financial data)
#   ./scripts/smoke_real.sh 600519 2022
#
# What it does:
#   - GET /healthz
#   - POST /jobs with the supplied params
#   - GET /jobs/{id}/stream and pipe SSE to stdout (Ctrl-C to stop)
#   - On done: GET /jobs/{id}/result and save the xlsx alongside
set -euo pipefail

HOST="${API_HOST:-http://127.0.0.1:8000}"
TOKEN="${API_TOKEN:-}"
STOCK="${1:?usage: smoke_real.sh <stock_code> <year> [financial_data_csv]}"
YEAR="${2:?usage: smoke_real.sh <stock_code> <year> [financial_data_csv]}"
FIN="${3:-}"

if ! command -v jq >/dev/null 2>&1; then
  echo "smoke_real.sh requires jq (brew install jq)" >&2
  exit 2
fi

# api_curl wraps curl and conditionally injects the bearer token.
# Wrapping avoids the bash 3.2 + `set -u` + empty-array-expansion trap:
#   "${arr[@]:-}" expands to one empty argument when arr is empty,
#   which curl rejects as "blank argument".
api_curl() {
  if [[ -n "$TOKEN" ]]; then
    curl "$@" -H "Authorization: Bearer $TOKEN"
  else
    curl "$@"
  fi
}

echo "==> healthz"
api_curl -fsS "$HOST/healthz" | jq -c .

echo "==> POST /jobs"
if [[ -n "$FIN" ]]; then
  payload=$(jq -nc --arg sc "$STOCK" --argjson yr "$YEAR" --arg fin "$FIN" \
    '{company_codes:[$sc], years:[$yr], financial_data_csv:$fin}')
else
  payload=$(jq -nc --arg sc "$STOCK" --argjson yr "$YEAR" \
    '{company_codes:[$sc], years:[$yr]}')
fi
echo "    payload: $payload"

resp=$(api_curl -fsS -X POST "$HOST/jobs" \
  -H "Content-Type: application/json" \
  -d "$payload")
job_id=$(echo "$resp" | jq -r '.job_id')
echo "    job_id=$job_id"

echo "==> GET /jobs/$job_id/stream  (Ctrl-C to detach; job keeps running)"
# `-N` disables curl buffering so SSE chunks arrive as they're sent.
# Comments (lines starting with `:`) are sse-starlette ping=15 heartbeats
# and should be ignored.
api_curl -N -fsS "$HOST/jobs/$job_id/stream" \
  | grep --line-buffered -vE '^:|^$' \
  || true

echo "==> GET /jobs/$job_id (final snapshot)"
final=$(api_curl -fsS "$HOST/jobs/$job_id")
echo "$final" | jq .
status=$(echo "$final" | jq -r '.status')

if [[ "$status" == "done" ]]; then
  out="data/results/smoke_${STOCK}_${YEAR}_${job_id:0:8}.xlsx"
  echo "==> GET /jobs/$job_id/result -> $out"
  api_curl -fsS "$HOST/jobs/$job_id/result" -o "$out"
  echo "    saved $(ls -lh "$out" | awk '{print $5,$9}')"
elif [[ "$status" == "error" ]]; then
  echo "    job ended in error: $(echo "$final" | jq -r '.error')" >&2
  exit 1
else
  echo "    job did not reach done (status=$status); skipping result download" >&2
fi
