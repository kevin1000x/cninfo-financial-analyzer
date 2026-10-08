import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { JobEvent, JobStatus } from "@/lib/types";
import { ApiError, cancelJob, streamUrl } from "@/lib/api";
import { advanceEventId } from "@/lib/sse";
import { cn } from "@/lib/utils";
import { Button, Chip } from "@/components/ui";

interface Props {
  jobId: string;
  onTerminal(status: "done" | "error", payload: JobEvent): void;
}

interface RenderEvent {
  key: number;
  event: JobEvent;
}

const PHASE_RE = /PHASE\s+(\d+):\s*(.+)/;
const SEPARATOR_RE = /^={5,}$/;
// Streaming engine's per-task marker: "[1/3] 600519 / 2023 / annual"
const TASK_LINE_RE = /^\[(\d+)\/(\d+)\]\s*(.+)/;
// Markers that the per-task loop has ended and export/save has begun.
const EXPORT_SEEN_RE =
  /Streaming task tally|Manifest:|Saved intermediate|PHASE 5|STREAMING PIPELINE COMPLETE/;

const STAGES = [{ name: "接收任务" }, { name: "逐份处理" }, { name: "汇总导出" }];

const STATUS_LABELS: Record<JobStatus, string> = {
  pending: "等待中",
  running: "运行中",
  done: "已完成",
  error: "失败",
  cancelled: "已取消",
};

const STATUS_TONES = {
  pending: "neutral",
  running: "accent",
  done: "success",
  error: "danger",
  cancelled: "neutral",
} as const;

// The parent renders this component with `key={jobId}`, so a job change
// remounts and re-initializes state. That means the SSE-open effect below
// can stay reset-free — it never has to clear out a previous job's events.
export function StreamView({ jobId, onTerminal }: Props) {
  const [events, setEvents] = useState<RenderEvent[]>([]);
  const [status, setStatus] = useState<JobStatus>("pending");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const seq = useRef(0);
  // Watermark for SSE dedupe. Scoped to the component instance, which is
  // scoped to the job (the parent renders with `key={jobId}`), so it survives
  // the reconnects it has to filter and resets with the job it belongs to.
  const lastEventId = useRef(0);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);

  useEffect(() => {
    const url = streamUrl(jobId);
    const es = new EventSource(url);

    const push = (event: JobEvent) => {
      setEvents((prev) => [...prev, { key: seq.current++, event }]);
    };

    const handler = (raw: MessageEvent<string>, fallbackType: string) => {
      // EventSource replays the whole history on every auto-reconnect.
      const nextId = advanceEventId(raw.lastEventId, lastEventId.current);
      if (nextId === null) return;
      lastEventId.current = nextId;

      try {
        const data = JSON.parse(raw.data) as JobEvent;
        push(data);
        if (data.type === "status") setStatus(data.status as JobStatus);
        if (data.type === "done") {
          setStatus("done");
          onTerminal("done", data);
        }
        if (data.type === "error") {
          setStatus("error");
          onTerminal("error", data);
        }
        if (data.type === "eof") es.close();
      } catch {
        push({
          type: "log",
          level: "WARN",
          message: `unparsed ${fallbackType}: ${raw.data}`,
          module: "frontend",
          ts: new Date().toISOString(),
        });
      }
    };

    // sse-starlette delivers each custom event name as a separate type to
    // addEventListener. The native 'error' channel and our server-emitted
    // 'error' event collide on the type signature; we accept Event and
    // narrow to MessageEvent via duck-typing (presence of `data`).
    const wrap = (fallbackType: string) => (e: Event) => {
      if ("data" in e && typeof (e as MessageEvent<string>).data === "string") {
        handler(e as MessageEvent<string>, fallbackType);
      }
    };
    es.addEventListener("status", wrap("status"));
    es.addEventListener("log", wrap("log"));
    es.addEventListener("done", wrap("done"));
    es.addEventListener("error", wrap("error"));
    es.addEventListener("eof", wrap("eof"));

    es.onerror = () => {
      // EventSource auto-reconnects on transient drops; surface persistent
      // errors only when the connection is fully closed.
      if (es.readyState === EventSource.CLOSED) {
        setStreamError("SSE 连接已关闭");
      }
    };

    return () => es.close();
  }, [jobId, onTerminal]);

  const active = status === "pending" || status === "running";

  // Elapsed timer: ticks while the job runs, freezes on the terminal state.
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(
      () => setElapsed((s) => s + 1),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [active]);

  // Auto-scroll follows the stream only while the user is at the bottom;
  // scrolling up to inspect history stops the yanking until they return.
  useEffect(() => {
    const el = containerRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  const { taskProgress } = useMemo(() => {
    let k = 0;
    let total = 0;
    let current = "";
    for (const { event } of events) {
      if (event.type !== "log") continue;
      const t = event.message.match(TASK_LINE_RE);
      if (t) {
        k = Number(t[1]);
        total = Number(t[2]);
        current = t[3].trim();
      }
    }
    return { taskProgress: { k, total, current } };
  }, [events]);

  const exportSeen = useMemo(
    () =>
      events.some(
        ({ event }) =>
          event.type === "log" && EXPORT_SEEN_RE.test(event.message),
      ),
    [events],
  );

  // The streaming engine interleaves download/parse/analyze per task, so the
  // honest track is: accept → process k/N → export. Reach marks the furthest
  // stage the job has entered; a settled job has reached all of them.
  const failed = status === "error" || status === "cancelled";
  const reach =
    status === "done" ? 3 : taskProgress.total > 0 ? (exportSeen ? 3 : 2) : 1;

  const handleCancel = async () => {
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelJob(jobId);
      // Nothing to update: the terminal `cancelled` status arrives over the
      // open SSE stream, and setting it here would race that event.
    } catch (err) {
      setCancelError(
        err instanceof ApiError
          ? `取消被拒绝（${err.status}）：任务可能已经结束`
          : "取消请求失败",
      );
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="overflow-hidden rounded-[10px] border border-border bg-surface shadow-[var(--shadow-panel)]">
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Chip
            tone={STATUS_TONES[status]}
            dot
            pulse={status === "running"}
            aria-live="polite"
          >
            {STATUS_LABELS[status]}
          </Chip>
          <span className="font-mono text-xs text-fg-4" title={jobId}>
            {jobId.slice(0, 8)}
          </span>
          <span className="font-mono text-xs text-fg-4 tabular-nums">
            {formatElapsed(elapsed)}
          </span>
          <span className="font-mono text-xs text-fg-4 tabular-nums">
            {events.length} 条事件
          </span>
        </div>
        <div className="flex items-center gap-3">
          {streamError && (
            <span className="text-xs text-red-600 dark:text-red-400">
              {streamError}
            </span>
          )}
          {cancelError && (
            <span className="text-xs text-amber-700 dark:text-amber-400">
              {cancelError}
            </span>
          )}
          {active && (
            <Button
              variant="danger-ghost"
              size="sm"
              onClick={() => void handleCancel()}
              disabled={cancelling}
            >
              {cancelling ? "取消中…" : "取消任务"}
            </Button>
          )}
        </div>
      </header>

      <ol
        aria-label="任务进度"
        className="flex items-center gap-2 border-b border-border px-4 py-3"
      >
        {STAGES.map((stage, i) => {
          const n = i + 1;
          const done = n < reach || status === "done";
          const current = !failed && status !== "done" && n === reach;
          const stalled = failed && n === reach;
          return (
            <li
              key={stage.name}
              aria-current={current ? "step" : undefined}
              className={cn(
                "flex items-center gap-2",
                i < STAGES.length - 1 && "flex-1",
              )}
            >
              <span className="flex items-center gap-1.5">
                <span
                  className={cn(
                    "grid h-5 w-5 shrink-0 place-items-center rounded-full border font-mono text-[10px] tabular-nums",
                    "transition-colors duration-200 ease-out-expo",
                    done && "border-transparent bg-accent text-accent-fg",
                    current && "border-accent text-accent ring-2 ring-accent/20",
                    !done && !current && "border-border text-fg-4",
                  )}
                >
                  {done ? "✓" : n}
                </span>
                <span
                  className={cn(
                    "text-xs whitespace-nowrap",
                    current ? "font-medium text-fg" : done ? "text-fg-2" : "text-fg-4",
                    stalled && "text-red-600 dark:text-red-400",
                  )}
                >
                  {stage.name}
                </span>
                {n === 2 && taskProgress.total > 0 && (
                  <span className="font-mono text-[10px] text-fg-3 tabular-nums">
                    {taskProgress.k}/{taskProgress.total}
                  </span>
                )}
              </span>
              {i < STAGES.length - 1 && (
                <span
                  aria-hidden="true"
                  className={cn(
                    "h-px flex-1 transition-colors duration-200 ease-out-expo",
                    n < reach || status === "done" ? "bg-accent/40" : "bg-border",
                  )}
                />
              )}
            </li>
          );
        })}
      </ol>

      {status === "running" && taskProgress.current && !exportSeen && (
        <div className="flex items-center gap-2 border-b border-border px-4 py-2 text-xs text-fg-3">
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent"
          />
          正在处理
          <span className="font-mono text-fg-2">{taskProgress.current}</span>
        </div>
      )}

      <div
        ref={containerRef}
        role="log"
        aria-live="off"
        aria-label="任务日志"
        onScroll={(e) => {
          const el = e.currentTarget;
          pinnedRef.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
        className="log-scroll max-h-[55vh] overflow-y-auto overscroll-contain bg-zinc-950 px-3 py-3 font-mono text-xs leading-relaxed"
      >
        {events.length === 0 && (
          <div className="flex items-center justify-center gap-2 py-10 text-zinc-500">
            <span
              aria-hidden="true"
              className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
            />
            连接中，正在回放历史事件…
          </div>
        )}
        {events.map(({ key, event }) => (
          <EventRow key={key} event={event} />
        ))}
      </div>
    </div>
  );
}

function formatElapsed(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

const PHASE_NAMES: Record<string, string> = {
  "1": "下载公告",
  "2": "解析 PDF",
  "3": "文本分析",
  "4": "TNI 指标",
  "5": "保存结果",
};

// Memoized: a long job appends hundreds of events, and each append re-renders
// the parent. Existing rows must not re-execute their render for every append.
const EventRow = memo(function EventRow({ event }: { event: JobEvent }) {
  if (event.type === "log") {
    const phaseMatch = event.message.match(PHASE_RE);
    const isSeparator = SEPARATOR_RE.test(event.message);
    if (isSeparator) {
      return <div className="my-1.5 border-t border-zinc-800/80" />;
    }
    if (phaseMatch) {
      const zh = PHASE_NAMES[phaseMatch[1]];
      return (
        <div className="my-2 flex items-center gap-2 text-emerald-400">
          <span aria-hidden="true">▸</span>
          <span className="font-semibold">
            PHASE {phaseMatch[1]}
            {zh && <span className="ml-2 font-medium text-zinc-400">{zh}</span>}
          </span>
          <span className="h-px flex-1 bg-zinc-800/80" aria-hidden="true" />
        </div>
      );
    }
    return (
      <div
        className={cn(
          "flex gap-2 py-px animate-log-in",
          event.level === "WARNING" && "text-amber-400",
          event.level === "ERROR" && "text-red-400",
          event.level !== "WARNING" &&
            event.level !== "ERROR" &&
            "text-zinc-400",
        )}
      >
        <span className="shrink-0 text-zinc-600 tabular-nums">
          {formatTs(event.ts)}
        </span>
        <span className="shrink-0 text-zinc-600">
          [{event.module}]
        </span>
        <span className="min-w-0 break-words text-zinc-300">
          {event.message}
        </span>
      </div>
    );
  }
  if (event.type === "status") {
    return (
      <div className="py-px text-zinc-500 animate-log-in">
        ● status → {event.status}
      </div>
    );
  }
  if (event.type === "done") {
    return (
      <div className="py-1 font-medium text-emerald-400 animate-log-in">
        ✓ done — rows={event.rows}, result={event.result_path}
      </div>
    );
  }
  if (event.type === "error") {
    return (
      <div className="py-1 font-medium text-red-400 animate-log-in">
        ✕ error — {event.error}
      </div>
    );
  }
  return null;
});

function formatTs(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
