import { authRequired } from "@/lib/auth";
import { useAuth } from "@/lib/authContext";
import { AuthPanel } from "@/components/AuthPanel";
import { useCallback, useEffect, useState } from "react";
import { CheckView } from "@/CheckView";
import { JobForm } from "@/components/JobForm";
import { StreamView } from "@/components/StreamView";
import { ResultCard } from "@/components/ResultCard";
import { Button, Chip, Logomark } from "@/components/ui";
import { createJob, getJob } from "@/lib/api";
import { loadActiveJobId, saveActiveJobId } from "@/lib/jobStore";
import type { CreateJobRequest, JobEvent } from "@/lib/types";

// Two views share a shell and user authentication. Their routes and engines
// remain separate within the integrated API:
//
//   批量分析   — cninfo pipeline behind /api/proxy/*. Stateful job runner,
//                one job at a time, SSE.
//   结论核查 — finaudit behind /api/audit/*. Stateless request/response.
//   The route id stays `audit` on purpose: the URL #/audit is already in
//   circulation (it is the demo link), and renaming it would break every
//   place that link was pasted. The label changed; the address did not.
//
// The tab is a real link plus the URL hash, not a router: the repo has no
// routing library on purpose, and <a href> is what makes Cmd-click,
// middle-click and the back button behave.
type View = "batch" | "audit";

const VIEWS: Array<{ id: View; label: string; href: string }> = [
  { id: "batch", label: "批量分析", href: "#/" },
  { id: "audit", label: "结论核查", href: "#/audit" },
];

function readHash(): View {
  return window.location.hash.replace(/^#\/?/, "") === "audit"
    ? "audit"
    : "batch";
}

interface FinalState {
  status: "done" | "error";
  rows: number;
  resultPath: string | null;
  error: string | null;
}

const PIPELINE_STEPS = [
  { name: "下载公告", detail: "巨潮资讯网批量抓取" },
  { name: "解析 PDF", detail: "定位 MD&A 节选" },
  { name: "文本分析", detail: "情感测度 · Fog 可读性" },
  { name: "TNI 指标", detail: "创新度 z-score（可选）" },
  { name: "保存结果", detail: "导出 xlsx 汇总表" },
];

function App() {
  const { session } = useAuth();
  const owner = session?.user.id ?? "legacy";
  const canRun = !authRequired || !!session;
  const [view, setView] = useState<View>(readHash);
  const [jobId, setJobId] = useState<string | null>(null);
  // Lazy init: if no stored id, we're already in the "boot complete" state
  // and the effect below has nothing to do — no synchronous setState in an
  // effect body. The async fetch path still flips this to true via setState
  // in an awaited callback, which is fine.
  const [bootChecked, setBootChecked] = useState<boolean>(
    () => !canRun || !loadActiveJobId(owner),
  );
  const [final, setFinal] = useState<FinalState | null>(null);

  // The hash is the source of truth; state only ever follows it. A
  // <button onClick> that swapped state instead would look identical and
  // quietly break Cmd-click, middle-click and the back button.
  useEffect(() => {
    const onHash = () => setView(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // On first render, see if we left a job behind in localStorage. Verify
  // the backend still knows about it before reattaching the stream;
  // if the server restarted, drop the stale id silently.
  useEffect(() => {
    const stored = canRun ? loadActiveJobId(owner) : null;
    if (!stored) return;
    let cancelled = false;

    void (async () => {
      try {
        const snap = await getJob(stored);
        if (cancelled) return;
        setJobId(snap.id);
        if (snap.status === "done" && snap.result_path) {
          setFinal({
            status: "done",
            rows: 0, // populated when SSE replays the done event
            resultPath: snap.result_path,
            error: null,
          });
        } else if (snap.status === "error") {
          setFinal({
            status: "error",
            rows: 0,
            resultPath: null,
            error: snap.error,
          });
        }
      } catch {
        saveActiveJobId(null, owner);
      } finally {
        if (!cancelled) setBootChecked(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [owner, canRun]);

  const handleSubmit = useCallback(async (req: CreateJobRequest) => {
    const resp = await createJob(req);
    saveActiveJobId(resp.job_id, owner);
    setJobId(resp.job_id);
    setFinal(null);
  }, [owner]);

  const handleTerminal = useCallback(
    (_status: "done" | "error", payload: JobEvent) => {
      if (payload.type === "done") {
        setFinal({
          status: "done",
          rows: payload.rows,
          resultPath: payload.result_path,
          error: null,
        });
      } else if (payload.type === "error") {
        setFinal({
          status: "error",
          rows: 0,
          resultPath: null,
          error: payload.error,
        });
      }
    },
    [],
  );

  const handleReset = useCallback(() => {
    saveActiveJobId(null, owner);
    setJobId(null);
    setFinal(null);
  }, [owner]);

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-10 border-b border-border bg-canvas/85 backdrop-blur-sm">
        <div className="mx-auto flex h-12 w-full max-w-4xl items-center justify-between px-4">
          <div className="flex items-center gap-2.5">
            <Logomark className="h-6 w-6" />
            <span className="text-sm font-semibold tracking-tight">
              CNINFO 研究台
            </span>
          </div>

          <nav aria-label="视图" className="flex items-center gap-0.5">
            {VIEWS.map((v) => (
              <a
                key={v.id}
                href={v.href}
                aria-current={v.id === view ? "page" : undefined}
                className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors duration-150 ease-out-expo focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas focus-visible:outline-none ${
                  v.id === view
                    ? "bg-surface-2 text-fg"
                    : "text-fg-3 hover:text-fg"
                }`}
              >
                {v.label}
              </a>
            ))}
          </nav>

          <a
            href="https://github.com/kevin1000x/cninfo-financial-analyzer"
            target="_blank"
            rel="noreferrer"
            className="text-xs font-medium text-fg-3 transition-colors duration-150 ease-out-expo hover:text-fg focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas focus-visible:outline-none rounded-sm hidden sm:inline"
          >
            GitHub ↗
          </a>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        <AuthPanel />
        {view === "audit" && (canRun ? <CheckView /> : <p className="rounded-md border border-border p-5 text-sm text-fg-2">登录后可核查结论，并查看当前可用的公司与年份。</p>)}

        {/*
          `bootChecked` gates only the batch view: it is waiting to hear whether
          a stored job id is still live. The audit view has nothing to restore,
          and blanking the whole page for it would be a regression the shell
          introduced.
        */}
        {view === "batch" && bootChecked && (
          <>
            {!jobId && (
              <section className="mb-10">
                <h1 className="text-[28px] leading-tight font-semibold tracking-tight text-balance">
                  年报文本分析工作台
                </h1>
                <p className="mt-2 max-w-xl text-sm leading-relaxed text-fg-2 text-pretty">
                  搜索公司名称、代码或拼音，选择年份范围，自动完成公告下载、PDF
                  解析、情感与可读性测度、TNI 创新度计算，并导出 xlsx 汇总表。
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Chip>数据源 · 巨潮资讯网</Chip>
                  <Chip>情感 · 可读性 · TNI</Chip>
                  <Chip>输出 · xlsx</Chip>
                </div>
              </section>
            )}

            {!jobId && <JobForm onSubmit={handleSubmit} disabled={!canRun} />}

            {jobId && (
              <div className="flex flex-col gap-4">
                <StreamView
                  key={jobId}
                  jobId={jobId}
                  onTerminal={handleTerminal}
                />
                {final?.status === "done" && (
                  <ResultCard
                    jobId={jobId}
                    rows={final.rows}
                    resultPath={final.resultPath}
                    onReset={handleReset}
                  />
                )}
                {final?.status === "error" && (
                  <div
                    role="alert"
                    className="rounded-[10px] border border-red-500/30 bg-surface p-5 shadow-[var(--shadow-panel)]"
                  >
                    <div className="flex items-center gap-2 text-sm font-medium text-red-600 dark:text-red-400">
                      <span aria-hidden="true">✕</span> 任务失败
                    </div>
                    <pre className="log-scroll mt-3 max-h-48 overflow-y-auto rounded-md border border-border bg-surface-2 p-3 font-mono text-xs whitespace-pre-wrap text-fg-2">
                      {final.error}
                    </pre>
                    <Button onClick={handleReset} className="mt-4">
                      新任务
                    </Button>
                  </div>
                )}
              </div>
            )}

            {!jobId && (
              <section aria-label="流水线阶段" className="mt-12">
                <h2 className="text-xs font-medium tracking-widest text-fg-4 uppercase">
                  流水线
                </h2>
                <ol className="mt-4 grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-5 sm:gap-x-3">
                  {PIPELINE_STEPS.map((step, i) => (
                    <li key={step.name} className="flex flex-col gap-1">
                      <span className="font-mono text-[11px] text-fg-4 tabular-nums">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span className="text-[13px] font-medium text-fg">
                        {step.name}
                      </span>
                      <span className="text-xs leading-snug text-fg-3">
                        {step.detail}
                      </span>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </>
        )}
      </main>

      <footer className="mx-auto w-full max-w-4xl px-4 pb-8 text-xs text-fg-4">
        数据来源：巨潮资讯网（CNINFO）· 分析结果仅供研究参考
      </footer>
    </div>
  );
}

export default function SessionApp() {
  const { session, ready } = useAuth();
  if (!ready) return <p role="status" className="p-8 text-sm text-fg-3">正在恢复研究账户…</p>;
  return <App key={session?.user.id ?? "guest"} />;
}
