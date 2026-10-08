// Client for the finaudit answering service, always hitting `/api/audit/...`.
//
// This is a SECOND backend, separate from the cninfo pipeline behind
// `/api/proxy/...`. They are not interchangeable:
//
//   /api/proxy/*  → cninfo FastAPI. Stateful job runner, single job at a
//                   time, returns 429 on concurrency, streams SSE.
//   /api/audit/*  → finaudit. Stateless request/response, no jobs, no
//                   streaming, no shared state between requests.
//
// Keeping them on separate routes (and separate Pages secrets) is deliberate:
// one is a long-running batch pipeline, the other must stay stateless — and
// merging them would quietly give the stateless one a job registry.

const BASE = "/api/audit";

/** One (company, year) we actually hold data for. */
export interface CoverageRow {
  /**
   * `"real"` — extracted from a CNINFO annual-report PDF, every field
   * hand-checked against the source.
   * `"synthetic"` — a fixture with made-up companies and made-up numbers.
   *
   * These must never be displayed as the same thing. Presenting synthetic
   * data as real is worse than the data itself being wrong.
   */
  nature: "real" | "synthetic";
  fixture_id: string;
  stock_code: string;
  fiscal_year: number;
  /** Only real rows carry one; fixtures' "company names" are invented. */
  short_name: string;
}

export interface Coverage {
  rows: CoverageRow[];
  counts: { real: number; synthetic: number; total: number };
  /** Server-authored. Render it verbatim — do not summarise or drop it. */
  notice: string;
}

export interface Refusal {
  refused: true;
  code: string;
  code_meaning: string;
  detail: string;
  metric_id: string | null;
  condition_index: number | null;
  source: string | null;
}

export interface Answer {
  question: string;
  question_sha256: string;
  metric_id: string | null;
  metric_version: number | null;
  entity: string | null;
  period: number | null;
  value: string | null;
  refused: boolean;
  refusal: Refusal | null;
  flags: string[];
  deferred_flags: string[];
  evidence: {
    data_source?: string | null;
    metric_definition_version?: number | null;
    execution_hash?: string | null;
  };
  gate: string | null;
}

export interface AnswerResponse {
  answer: Answer;
  /**
   * The evidence page, already rendered by the server.
   *
   * 🔴 Render this VERBATIM. Do not reformat it, do not parse it into
   * components, do not "improve" it here. There is exactly one human-readable
   * contract for an answer and it lives server-side; a second rendering in the
   * browser would be a version nobody has ever reviewed.
   */
  page: string;
  /** Present when the refusal is "we don't have this company/year". */
  coverage_note?: string;
}

/**
 * One verdict on one claim. The set is CLOSED at five (`D-041` §3) — if a
 * sixth ever shows up here, the server changed a decision, not a detail.
 *
 * `NOT_COVERED` and `NOT_CHECKABLE` are SUCCESSFUL outcomes, not errors.
 * "We can't check this" is the honest answer when coverage is deliberately
 * narrow, and the UI must not style them as failures.
 */
export type Verdict =
  | "CONSISTENT"
  | "INCONSISTENT"
  | "AMBIGUOUS_BASIS"
  | "NOT_COVERED"
  | "NOT_CHECKABLE";

/** What the server decided the whole paste was. Shown first, never hidden. */
export type ReadAs = "QUESTION" | "STATEMENT";

export interface Claim {
  text: string;
  verdict: Verdict;
  verdict_meaning: string;
  /** What was actually sent downstream — differs from `text` when context was inherited. */
  asked: string | null;
  stated_value: string | null;
  stated_text: string | null;
  reason: string;
  /**
   * Non-null when subject/period were carried over from an earlier sentence.
   *
   * 🔴 Render it. If the system inherited the WRONG company or year, the
   * reader has to be able to see that at a glance — that is the whole point
   * of printing how the input was read.
   */
  inherited: string | null;
  answer: Answer | null;
}

export interface VerifyReport {
  text: string;
  read_as: ReadAs;
  claims: Claim[];
  /** All five keys always present — a missing key and a zero are not the same thing. */
  counts: Record<Verdict, number>;
  /** Only when `read_as === "QUESTION"`: the plain answering path, unchanged. */
  answer: Answer | null;
}

export interface VerifyResponse {
  report: VerifyReport;
  /**
   * The report page, already rendered by the server.
   *
   * 🔴 Same rule as `AnswerResponse.page`: render VERBATIM. There is exactly
   * one human-readable contract and it lives server-side (`render_report`).
   */
  page: string;
  /** Present when a model was configured but unreachable this request. */
  model_note?: string;
}

export class AuditApiError extends Error {
  public readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function asJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed && typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // not JSON; keep the raw text
    }
    throw new AuditApiError(resp.status, detail || `HTTP ${resp.status}`);
  }
  return (await resp.json()) as T;
}

export async function getCoverage(signal?: AbortSignal): Promise<Coverage> {
  return asJson<Coverage>(await fetch(`${BASE}/coverage`, { signal }));
}

export async function ask(
  question: string,
  signal?: AbortSignal,
): Promise<AnswerResponse> {
  const resp = await fetch(`${BASE}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // Only a question string goes over this wire. There is deliberately no
    // file upload, no CSV, no pasted table: the service answers from public
    // annual reports only, and accepting a user's own financial data would
    // remove the compliance premise the whole project rests on.
    body: JSON.stringify({ question }),
    signal,
  });
  return asJson<AnswerResponse>(resp);
}

/**
 * The one input box (`D-041` §1).
 *
 * Send a paragraph; the server decides whether it is a question or a set of
 * claims to check. There is deliberately no mode flag on this call: checking
 * is a SUPERSET of asking (to check "净利率 50.6%" the system must compute
 * 净利率 first), so a client-side toggle would be a second, competing answer
 * to a question the server already answers — and the server's answer is the
 * one printed on the report.
 */
export async function verify(
  text: string,
  signal?: AbortSignal,
): Promise<VerifyResponse> {
  const resp = await fetch(`${BASE}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // One string. No file upload, no CSV, no pasted spreadsheet.
    // A pasted CONCLUSION is a claim to be judged; a pasted STATEMENT OF
    // ACCOUNTS would be the user's own financial data, and accepting it would
    // remove the compliance premise the whole project rests on (`D-010`).
    body: JSON.stringify({ text }),
    signal,
  });
  return asJson<VerifyResponse>(resp);
}

/** Group coverage rows by company so the panel lists companies, not rows. */
export function byCompany(rows: CoverageRow[]): Array<{
  nature: CoverageRow["nature"];
  stock_code: string;
  short_name: string;
  years: number[];
}> {
  const map = new Map<
    string,
    {
      nature: CoverageRow["nature"];
      stock_code: string;
      short_name: string;
      years: number[];
    }
  >();
  for (const r of rows) {
    // Key on nature too: a synthetic 900001 and a real 900001 would be
    // different things, and merging them would erase exactly the distinction
    // this panel exists to show.
    const key = `${r.nature}:${r.stock_code}`;
    const found = map.get(key);
    if (found) {
      if (!found.years.includes(r.fiscal_year)) found.years.push(r.fiscal_year);
      if (!found.short_name && r.short_name) found.short_name = r.short_name;
    } else {
      map.set(key, {
        nature: r.nature,
        stock_code: r.stock_code,
        short_name: r.short_name,
        years: [r.fiscal_year],
      });
    }
  }
  const out = [...map.values()];
  for (const c of out) c.years.sort((a, b) => a - b);
  // Real first — it is what the product is actually for; fixtures are a
  // demonstration of behaviour, not a claim of coverage.
  out.sort((a, b) =>
    a.nature === b.nature
      ? a.stock_code.localeCompare(b.stock_code)
      : a.nature === "real"
        ? -1
        : 1,
  );
  return out;
}
