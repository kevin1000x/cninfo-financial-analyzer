import { useMemo } from "react";
import { Chip } from "@/components/ui";
import { byCompany, type Coverage } from "@/lib/auditApi";

interface Props {
  coverage: Coverage | null;
  error: string | null;
  onPick(stockCode: string, year: number): void;
}

/*
 * Scope before input (see DESIGN.md § 结论核查).
 *
 * The product's whole claim is that every answer is auditable. The fastest way
 * to lose that claim is to look like it can answer about any company, then
 * answer about one it has no data for. So the scope limitation is the first
 * thing on the page — the same instinct as declaring a scope limitation at the
 * top of an audit report rather than in a note at the back.
 */

export function CoveragePanel({ coverage, error, onPick }: Props) {
  // The parent holds the question text, so this panel re-renders on every
  // keystroke. No React Compiler in this build, so memo it by hand.
  // Must sit above the early returns: hook order cannot depend on a branch.
  const companies = useMemo(() => byCompany(coverage?.rows ?? []), [coverage]);

  if (error) {
    return (
      <section
        role="alert"
        className="rounded-[10px] border border-red-500/30 bg-surface p-4 shadow-[var(--shadow-panel)]"
      >
        <div className="text-sm font-medium text-red-600 dark:text-red-400">
          取不到覆盖清单
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-fg-2">
          {error}
          <br />
          清单没加载出来之前不要提问 ——
          不知道有哪些数据，答案是真是假也判断不了。
        </p>
      </section>
    );
  }

  if (!coverage) {
    return (
      <section className="rounded-[10px] border border-border bg-surface p-4 text-sm text-fg-3 shadow-[var(--shadow-panel)]">
        正在读取覆盖清单…
      </section>
    );
  }

  const real = companies.filter((c) => c.nature === "real");
  const synthetic = companies.filter((c) => c.nature === "synthetic");

  return (
    <section className="rounded-[10px] border border-border bg-surface shadow-[var(--shadow-panel)]">
      <header className="border-b border-border px-4 py-2.5">
        <h2 className="text-xs font-medium tracking-widest text-fg-4 uppercase">
          我们手上有哪些数据
        </h2>
      </header>

      <div className="px-4 py-3.5">
        {/* Server-authored: the counts and the caveat are one sentence written
            by the side that knows the truth. Rendered verbatim. */}
        <p className="text-[13px] leading-relaxed text-fg-2 text-pretty">
          {coverage.notice}
        </p>

        {real.length > 0 ? (
          <Group
            label="真实年报"
            hint="巨潮资讯网年报原文，逐字段人工核对过"
            tone="real"
            companies={real}
            onPick={onPick}
          />
        ) : null}
        {synthetic.length > 0 ? (
          <Group
            label="合成夹具"
            hint="虚构公司与虚构数值，用来演示行为"
            tone="synthetic"
            companies={synthetic}
            onPick={onPick}
          />
        ) : null}

        <p className="mt-4 border-t border-border pt-3 text-xs leading-relaxed text-fg-3">
          清单之外的公司会得到一次说明原因的拒答，
          <span className="font-medium text-fg-2">不会得到一个编出来的数</span>
          。
          「我们没有这家公司」与「这家公司没有这个指标」是两件事，页面上分开说。
        </p>
      </div>
    </section>
  );
}

function Group({
  label,
  hint,
  tone,
  companies,
  onPick,
}: {
  label: string;
  hint: string;
  tone: "real" | "synthetic";
  companies: ReturnType<typeof byCompany>;
  onPick(stockCode: string, year: number): void;
}) {
  // Real and synthetic get visibly different chrome, and not by colour alone —
  // synthetic is also dashed. Same-looking chips would put the burden of
  // remembering which is which on the reader, and that is exactly the mistake
  // that turns a fixture into a claim about a real company.
  const chip =
    tone === "real"
      ? "border-border-strong bg-surface hover:border-fg-4"
      : "border-dashed border-amber-500/40 bg-amber-500/[0.06] hover:border-amber-500/70";

  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-baseline gap-x-2">
        {/* shrink-0: without it the four-character label gets squeezed by the
            hint and breaks mid-word on a phone ("合成夹 / 具"). */}
        <span className="shrink-0 text-[13px] font-medium text-fg">
          {label}
        </span>
        <span className="text-xs text-fg-3">{hint}</span>
        {tone === "synthetic" ? (
          <Chip tone="warn" className="ml-auto">
            不是真实公司
          </Chip>
        ) : null}
      </div>

      {/*
        One <li> per chip, not one <li> per company with `display: contents`:
        that strips list semantics in several browsers, and the count ("list,
        6 items") is the one bit of structure that says how much coverage
        there actually is.
      */}
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {companies.flatMap((c) =>
          c.years.map((y) => (
            <li key={`${c.nature}:${c.stock_code}:${y}`}>
              <button
                type="button"
                onClick={() => onPick(c.stock_code, y)}
                aria-label={`把 ${c.short_name || c.stock_code} ${y} 年填进提问框`}
                className={`cursor-pointer touch-manipulation rounded-md border px-2 py-1 text-xs transition-[transform,border-color,background-color] duration-150 ease-out-expo active:scale-[0.97] focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas focus-visible:outline-none ${chip}`}
              >
                <span className="font-mono tabular-nums">{c.stock_code}</span>
                {c.short_name ? (
                  <span className="ml-1.5 text-fg-2">{c.short_name}</span>
                ) : null}
                <span className="ml-1.5 font-mono text-fg-3 tabular-nums">
                  {y}
                </span>
              </button>
            </li>
          )),
        )}
      </ul>
    </div>
  );
}
