import { useState } from "react";
import { downloadResult } from "@/lib/api";
import { Button } from "@/components/ui";

interface Props {
  jobId: string;
  rows: number;
  resultPath: string | null;
  onReset(): void;
}

export function ResultCard({ jobId, rows, resultPath, onReset }: Props) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  async function handleDownload() {
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadResult(jobId);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="rounded-[10px] border border-border bg-surface p-5 shadow-[var(--shadow-panel)] sm:p-6">
      <div className="flex items-center gap-2 text-sm font-medium text-emerald-700 dark:text-emerald-400">
        <svg
          aria-hidden="true"
          viewBox="0 0 16 16"
          className="h-4 w-4"
          fill="none"
        >
          <circle cx="8" cy="8" r="7" stroke="currentColor" strokeOpacity="0.35" />
          <path
            d="M5 8.2 7.2 10.4 11 5.8"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        分析完成
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-3">
        <div>
          <div className="text-3xl leading-none font-semibold tracking-tight tabular-nums">
            {rows}
          </div>
          <div className="mt-1.5 text-xs text-fg-3">条观测值</div>
        </div>
        {resultPath && (
          <div className="min-w-0 max-w-full flex-1 basis-64">
            <div className="text-xs text-fg-3">结果文件</div>
            <code
              className="mt-1 block truncate rounded-md border border-border bg-surface-2 px-2 py-1 font-mono text-xs text-fg-2"
              title={resultPath}
            >
              {resultPath}
            </code>
          </div>
        )}
      </div>

      {rows < 2 && (
        <div className="mt-4 rounded-md border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-700 dark:text-amber-400">
          z-score 标准化需要 ≥ 2 条观测，单条结果中 TNI 列会是 nan
        </div>
      )}

      <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4">
        <Button
          variant="primary"
          onClick={() => void handleDownload()}
          disabled={downloading}
        >
          {downloading ? "下载中…" : "下载 xlsx"}
        </Button>
        <Button onClick={onReset}>新任务</Button>
      </div>

      {downloadError && (
        <div
          role="alert"
          className="mt-4 rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-700 dark:text-red-300"
        >
          {downloadError}
        </div>
      )}
    </div>
  );
}
