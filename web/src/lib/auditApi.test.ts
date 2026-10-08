import { afterEach, describe, expect, it, vi } from "vitest";
import { byCompany, verify, type CoverageRow } from "./auditApi";


// Transport contract tests use an explicit auth boundary; real local .env files
// must not decide whether these mocked HTTP requests are allowed. auth.test.ts
// separately exercises configured, signed-out and token-bearing sessions.
vi.mock("./auth", () => ({
  authenticatedFetch: (input: RequestInfo | URL, init?: RequestInit) =>
    fetch(input, { ...init, headers: new Headers(init?.headers) }),
}));

afterEach(() => vi.unstubAllGlobals());

function row(p: Partial<CoverageRow>): CoverageRow {
  return {
    nature: "synthetic",
    fixture_id: "f",
    stock_code: "900001",
    fiscal_year: 2023,
    short_name: "",
    ...p,
  };
}

describe("byCompany", () => {
  it("groups a company's years into one entry", () => {
    const out = byCompany([
      row({
        nature: "real",
        stock_code: "600519",
        fiscal_year: 2023,
        short_name: "贵州茅台",
      }),
      row({
        nature: "real",
        stock_code: "600519",
        fiscal_year: 2022,
        short_name: "贵州茅台",
      }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].years).toEqual([2022, 2023]);
    expect(out[0].short_name).toBe("贵州茅台");
  });

  it("never merges a real company with a synthetic one that shares its code", () => {
    // What going red looks like: a fixture using a code that a real company
    // also uses gets folded into the real entry, and the page then shows made-up
    // numbers under a real company's name. Presenting synthetic data as real is
    // worse than the data being wrong — it is the one mistake this product
    // cannot afford.
    const out = byCompany([
      row({ nature: "real", stock_code: "600519", short_name: "贵州茅台" }),
      row({ nature: "synthetic", stock_code: "600519", short_name: "" }),
    ]);
    expect(out).toHaveLength(2);
    expect(out.map((c) => c.nature)).toEqual(["real", "synthetic"]);
  });

  it("lists real companies before synthetic ones", () => {
    const out = byCompany([
      row({ nature: "synthetic", stock_code: "900001" }),
      row({ nature: "real", stock_code: "600309", short_name: "万华化学" }),
    ]);
    expect(out[0].nature).toBe("real");
  });

  it("keeps years sorted even when the server returns them out of order", () => {
    const out = byCompany([
      row({ stock_code: "900001", fiscal_year: 2024 }),
      row({ stock_code: "900001", fiscal_year: 2019 }),
      row({ stock_code: "900001", fiscal_year: 2021 }),
    ]);
    expect(out[0].years).toEqual([2019, 2021, 2024]);
  });

  it("does not duplicate a year the server listed twice", () => {
    const out = byCompany([
      row({ stock_code: "900001", fiscal_year: 2023 }),
      row({ stock_code: "900001", fiscal_year: 2023 }),
    ]);
    expect(out[0].years).toEqual([2023]);
  });
});

describe("verify", () => {
  /*
   * These pin the wire contract, not the UI. What matters here is that the
   * client sends ONE string and nothing else: `D-010` draws the line at
   * "a pasted conclusion is a claim to be judged, a pasted set of accounts is
   * the user's own financial data". If a second content field ever appears in
   * this body, that line has moved.
   */
  function stubFetch(status: number, body: unknown) {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", ((url: string, init: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }) as unknown as typeof fetch);
    return calls;
  }

  const report = {
    text: "600519 2023 年资产负债率 17.98%",
    read_as: "STATEMENT" as const,
    claims: [],
    counts: {
      CONSISTENT: 1,
      INCONSISTENT: 0,
      AMBIGUOUS_BASIS: 0,
      NOT_COVERED: 0,
      NOT_CHECKABLE: 0,
    },
    answer: null,
  };

  it("posts exactly one field, named text", async () => {
    const calls = stubFetch(200, { report, page: "我把这段话读成：待核声明" });
    await verify("600519 2023 年资产负债率 17.98%");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/audit/verify");
    const sent = JSON.parse(String(calls[0].init.body)) as Record<
      string,
      unknown
    >;
    // Exactly one key. A file, a table or a rows[] would each be a new key.
    expect(Object.keys(sent)).toEqual(["text"]);
  });

  it("returns the server-rendered page verbatim", async () => {
    const page = "我把这段话读成：待核声明\n\n拆出 1 条，逐条判定：";
    stubFetch(200, { report, page });
    const got = await verify("x");
    // Verbatim: there is exactly one human-readable contract and it is
    // server-side. Any transformation here would be a second version.
    expect(got.page).toBe(page);
  });

  it("surfaces a 4xx as an AuditApiError carrying the server's detail", async () => {
    stubFetch(400, { detail: "text 要是一个非空字符串" });
    await expect(verify("")).rejects.toMatchObject({
      status: 400,
      message: "text 要是一个非空字符串",
    });
  });

  it("keeps all five verdict counts, including the zeros", async () => {
    stubFetch(200, { report, page: "" });
    const got = await verify("x");
    // A missing key and a zero are not the same thing to a reader.
    expect(Object.keys(got.report.counts).sort()).toEqual([
      "AMBIGUOUS_BASIS",
      "CONSISTENT",
      "INCONSISTENT",
      "NOT_CHECKABLE",
      "NOT_COVERED",
    ]);
  });
});
