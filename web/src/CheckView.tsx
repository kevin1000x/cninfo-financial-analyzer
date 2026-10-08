import { useCallback, useEffect, useRef, useState } from "react";
import { AskForm } from "@/components/AskForm";
import { CoveragePanel } from "@/components/CoveragePanel";
import { VerdictList } from "@/components/VerdictList";
import { Chip } from "@/components/ui";
import {
  getCoverage,
  verify,
  type Coverage,
  type VerifyResponse,
} from "@/lib/auditApi";

/** Mirrors `service.api.MAX_VERIFY_BYTES`. */
const MAX_VERIFY_BYTES = 8000;

/*
 * The conclusion-checking entry (`D-041`).
 *
 * ONE input box. The server decides whether what you pasted is a question or
 * a set of claims — there is no mode toggle here, and adding one would be a
 * second answer to a question the server already answers.
 *
 * Why one box works: checking is a SUPERSET of asking. To check
 * "净利率 50.6%" the system has to compute 净利率 first, so the old
 * question-and-answer path did not go away — it is inside the check, and the
 * only difference is whether you brought a number with you.
 */
export function CheckView() {
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [result, setResult] = useState<VerifyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inflight = useRef<AbortController | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    void (async () => {
      try {
        setCoverage(await getCoverage(ctl.signal));
      } catch (err) {
        if (ctl.signal.aborted) return;
        setCoverageError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => ctl.abort();
  }, []);

  useEffect(() => () => inflight.current?.abort(), []);

  const submit = useCallback(async (t: string) => {
    inflight.current?.abort();
    const ctl = new AbortController();
    inflight.current = ctl;
    setBusy(true);
    setError(null);
    try {
      const resp = await verify(t, ctl.signal);
      if (ctl.signal.aborted) return;
      setResult(resp);
    } catch (err) {
      if (ctl.signal.aborted) return;
      // "核不了" arrives as 200 + a report. Anything landing here is
      // transport, not the service declining — say which. Conflating them is
      // exactly the confusion this product exists to prevent.
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!ctl.signal.aborted) setBusy(false);
    }
  }, []);

  const pick = useCallback((code: string, year: number) => {
    setText(`${code} ${year} 年的资产负债率是多少？`);
  }, []);

  const report = result?.report ?? null;

  return (
    <div className="flex flex-col gap-6">
      <section>
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight text-balance">
          结论核查
        </h1>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-fg-2 text-pretty">
          把一段财报分析结论粘进来 —— 券商 AI、问财、雪球，或者任何一个大模型写的。
          系统把它拆成可核声明<span className="font-medium text-fg">逐条判定</span>
          ，每一条都附一条可独立复核的证据链：用了哪张表、哪个口径、读到的字段取值是多少、
          年报原文的 SHA-256 是什么。
          {/*
            What this page must never say: "经复核验证", or an agreement rate.
            The evidence-chain review (H2) has not passed with the numbers this
            page would be quoting, and claiming a result we don't have would be
            the exact failure this product exists to prevent.
          */}
        </p>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-fg-2 text-pretty">
          <span className="font-medium text-fg">「核不了」是正当输出，不是失败。</span>
          覆盖面窄是刻意的 —— 换来的是每一个能核的数都有人逐字段核对并签过字。
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Chip>数据源 · 巨潮年报原文</Chip>
          <Chip>逐字段人工核对</Chip>
          <Chip>核不了也说清为什么</Chip>
        </div>
      </section>

      {/* Scope before input. See the note in CoveragePanel. */}
      <CoveragePanel coverage={coverage} error={coverageError} onPick={pick} />

      <AskForm
        value={text}
        onChange={setText}
        onSubmit={submit}
        busy={busy}
        maxBytes={MAX_VERIFY_BYTES}
        label="粘一段结论，或者直接问一个问题"
        placeholder="例如：贵州茅台 2023 年净利率 50.6%，同比提升 1.2 个百分点，盈利能力持续增强。"
        submitLabel="核一遍"
        busyLabel="正在核…"
        emptyHint="先粘一段结论，或写一个问题。"
        hint="只接受一段文字 —— 没有文件上传，不收你自己的报表"
        rows={4}
      />

      <div aria-live="polite" aria-busy={busy}>
        {error ? (
          <div
            role="alert"
            className="rounded-[10px] border border-red-500/30 bg-surface p-4 shadow-[var(--shadow-panel)]"
          >
            <div className="text-sm font-medium text-red-600 dark:text-red-400">
              没能问到服务
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-fg-2">
              {error}
              <br />
              这是<span className="font-medium text-fg">连不上</span>
              ，不是「它核不了」—— 核不了会带着理由正常返回。
              <br />
              请稍后重试。已输入的结论会保留在当前页面。
            </p>
          </div>
        ) : null}

        {report && !error ? (
          <article className="overflow-hidden rounded-[10px] border border-border bg-surface shadow-[var(--shadow-panel)]">
            {/*
              🔴 First line, always: what the system read this as.
              Same idea as printing `matched_alias` on the evidence page — the
              reader has to be able to see a misreading at a glance instead of
              receiving a result that makes no sense to them.
            */}
            <header className="border-b border-border px-4 py-3.5">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
                <span className="text-xs font-medium tracking-widest text-fg-4 uppercase">
                  我把这段话读成
                </span>
                <span className="text-base font-semibold text-fg">
                  {report.read_as === "QUESTION" ? "一个提问" : "待核声明"}
                </span>
                {report.read_as === "STATEMENT" ? (
                  <span className="ml-auto font-mono text-xs text-fg-3 tabular-nums">
                    拆出 {report.claims.length} 条
                  </span>
                ) : null}
              </div>
              {report.read_as === "QUESTION" ? (
                <p className="mt-1.5 text-xs leading-relaxed text-fg-2">
                  这段话里没有待核的数值，所以它是一个提问 —— 走的是问答路径。
                </p>
              ) : null}
            </header>

            {result?.model_note ? (
              <div className="border-b border-border px-4 py-3 text-[13px] leading-relaxed text-fg-2 text-pretty">
                {result.model_note}
              </div>
            ) : null}

            {report.read_as === "STATEMENT" ? (
              <div className="border-b border-border px-4 py-3.5">
                <VerdictList report={report} />
              </div>
            ) : null}

            <div className="px-4 py-3.5">
              <h3 className="text-xs font-medium tracking-widest text-fg-4 uppercase">
                核查报告与证据链
              </h3>
              {/*
                Verbatim server output. `whitespace-pre-wrap` keeps the
                server's own line breaks and indentation — they carry the
                structure, and re-flowing here would destroy the alignment.
              */}
              <pre className="log-scroll mt-2.5 max-h-[32rem] overflow-auto rounded-md border border-border bg-surface-2 p-3 font-mono text-xs leading-[1.75] whitespace-pre-wrap text-fg-2">
                {result?.page}
              </pre>
            </div>
          </article>
        ) : null}
      </div>
    </div>
  );
}
