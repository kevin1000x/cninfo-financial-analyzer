import { useMemo, useState } from "react";
import type { CreateJobRequest } from "@/lib/types";
import {
  financialDataInput,
  type FinancialDataMode,
} from "@/lib/financialData";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui";
import { StockPicker } from "./StockPicker";
import { parseStockCodes, type Stock } from "@/lib/stocks";

const REPORT_TYPES = ["annual", "semi_annual", "quarterly"] as const;
type ReportType = (typeof REPORT_TYPES)[number];

const REPORT_TYPE_LABELS: Record<ReportType, string> = {
  annual: "年报",
  semi_annual: "半年报",
  quarterly: "季报",
};

const TNI_MODES = [
  {
    value: "akshare" as const,
    label: "自动获取 AKShare 财务数据",
    hint: "后端将获取年度 ROA 与经营现金流，并在 Supabase 可用时缓存；首次请求可能较慢",
  },
  {
    value: "skip" as const,
    label: "不计算 TNI",
    hint: "只跑情感与可读性，最快",
  },
  {
    value: "examples" as const,
    label: "使用 examples/financial_data.csv",
    hint: "示例财务数据仅含 000001 / 000002 / 600000 / 600036 三年（2020-2022）；其它代码或年份会得到 NaN TNI",
  },
];

const CURRENT_YEAR = new Date().getFullYear();
const MIN_YEAR = 1990;

// Default to the latest three complete reporting years. Example data is fixed.
const RECENT_YEARS = { start: CURRENT_YEAR - 3, end: CURRENT_YEAR - 1 };
const EXAMPLES_TNI_YEARS = { start: 2020, end: 2022 };

interface Props {
  onSubmit(req: CreateJobRequest): Promise<void> | void;
  disabled?: boolean;
}

export function JobForm({ onSubmit, disabled }: Props) {
  const [codesText, setCodesText] = useState("");
  const [selectedStocks, setSelectedStocks] = useState<Stock[]>([]);
  const [yearStart, setYearStart] = useState(RECENT_YEARS.start);
  const [yearEnd, setYearEnd] = useState(RECENT_YEARS.end);
  const [reportType, setReportType] = useState<ReportType>("annual");
  const [tniMode, setTniMode] = useState<FinancialDataMode>("akshare");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const codes = useMemo(() => {
    const batch = parseStockCodes(codesText);
    return { valid: [...new Set([...selectedStocks.map(stock => stock.code), ...batch.valid])], invalid: batch.invalid };
  }, [codesText, selectedStocks]);
  // Match the catalogue market boundary even when codes are pasted directly.
  const unsupported = codes.valid.filter(code => /^(?:4|8|92)/.test(code) || !/^[02369]\d{5}$/.test(code));
  const codeError = codes.invalid.length
    ? `不是 6 位数字代码：${codes.invalid.slice(0, 3).join(", ")}`
    : unsupported.length
      ? `暂不支持北交所或未识别市场的代码：${unsupported.slice(0, 3).join("、")}。请移除后再提交。`
    : codes.valid.length === 0
      ? "搜索并选择公司，或粘贴一组股票代码"
      : null;

  const yearRangeError =
    yearStart > yearEnd
      ? "起始年不能晚于结束年"
      : yearStart < MIN_YEAR || yearEnd > CURRENT_YEAR + 1
        ? `年份必须在 ${MIN_YEAR} 与 ${CURRENT_YEAR + 1} 之间`
        : null;

  const years = useMemo(() => {
    if (yearRangeError) return [];
    const out: number[] = [];
    for (let y = yearStart; y <= yearEnd; y++) out.push(y);
    return out;
  }, [yearStart, yearEnd, yearRangeError]);

  const totalTasks = codes.valid.length * years.length;
  const overLimit = totalTasks > 100;

  const canSubmit =
    !disabled &&
    !submitting &&
    !codeError &&
    !yearRangeError &&
    !overLimit &&
    codes.valid.length > 0 &&
    years.length > 0;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    const req: CreateJobRequest = {
      company_codes: codes.valid,
      years,
      report_types: [reportType],
      ...financialDataInput(tniMode),
    };

    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(req);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-6 rounded-[10px] border border-border bg-surface p-5 shadow-[var(--shadow-panel)] sm:p-6"
    >
      <StockPicker selected={selectedStocks} onChange={setSelectedStocks} />
      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer text-xs font-medium text-fg-2">批量粘贴股票代码</summary>
        <div className="mt-3">
      <Field
        id="codes"
        label="股票代码"
        hint="支持沪深代码；北交所暂不可提交"
        error={codeError}
      >
        <textarea
          id="codes"
          name="company-codes"
          rows={3}
          value={codesText}
          onChange={(e) => setCodesText(e.target.value)}
          placeholder="如 600000  600519"
          spellCheck={false}
          autoComplete="off"
          aria-describedby="codes-count"
          className={cn(
            "w-full resize-y rounded-md border bg-surface-2 px-3 py-2.5",
            "font-mono text-sm tracking-wide transition-colors duration-150 ease-out-expo",
            "placeholder:text-fg-4",
            "focus:border-accent focus:ring-2 focus:ring-accent/20 focus:outline-none",
            codeError
              ? "border-red-500/60 focus:border-red-500 focus:ring-red-500/15"
              : "border-border-strong",
          )}
        />
        <div id="codes-count" className="text-xs text-fg-3">
          已识别{" "}
          <strong className="font-mono font-medium text-fg tabular-nums">
            {codes.valid.length}
          </strong>{" "}
          个
          {codes.invalid.length > 0 && (
            <span className="ml-2 text-red-600 dark:text-red-400">
              请修正 {codes.invalid.length} 个无效输入
            </span>
          )}
        </div>
      </Field>
        </div>
      </details>
      {codeError && <p className="text-xs text-fg-3">{codeError}</p>}

      <div className="grid gap-6 sm:grid-cols-2">
        <Field
          id="year-start"
          label="年份范围"
          error={yearRangeError}
          hint={yearRangeError ? undefined : `共 ${years.length} 年`}
        >
          <div className="flex items-center gap-2">
            <YearInput
              id="year-start"
              value={yearStart}
              onChange={setYearStart}
              invalid={!!yearRangeError}
              ariaLabel="起始年份"
            />
            <span className="text-xs text-fg-4">至</span>
            <YearInput
              id="year-end"
              value={yearEnd}
              onChange={setYearEnd}
              invalid={!!yearRangeError}
              ariaLabel="结束年份"
            />
          </div>
        </Field>

        <Field id="report-type-seg" label="报告类型">
          <div
            id="report-type-seg"
            role="group"
            aria-label="报告类型"
            className="flex rounded-md border border-border bg-surface-2 p-0.5"
          >
            {REPORT_TYPES.map((rt) => (
              <button
                type="button"
                key={rt}
                onClick={() => setReportType(rt)}
                aria-pressed={reportType === rt}
                className={cn(
                  "flex-1 cursor-pointer rounded-[5px] px-2 py-1.5 text-[13px] font-medium",
                  "transition-colors duration-150 ease-out-expo",
                  "focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:outline-none",
                  reportType === rt
                    ? "bg-surface text-fg shadow-sm"
                    : "text-fg-3 hover:text-fg",
                )}
              >
                {REPORT_TYPE_LABELS[rt]}
              </button>
            ))}
          </div>
        </Field>
      </div>

      <Field id="tni-group" label="TNI 计算">
        <div
          id="tni-group"
          role="radiogroup"
          aria-label="TNI 计算方式"
          className="flex flex-col gap-2"
        >
          {TNI_MODES.map((m) => {
            const selected = tniMode === m.value;
            return (
              <label
                key={m.value}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-md border p-3",
                  "transition-colors duration-150 ease-out-expo",
                  "focus-within:ring-2 focus-within:ring-accent/40 focus-within:outline-none",
                  selected
                    ? "border-accent/50 bg-accent-soft"
                    : "border-border hover:border-border-strong",
                )}
              >
                <input
                  type="radio"
                  name="tni"
                  value={m.value}
                  checked={selected}
                  onChange={() => {
                    setTniMode(m.value);
                    if (m.value === "examples") {
                      setYearStart(EXAMPLES_TNI_YEARS.start);
                      setYearEnd(EXAMPLES_TNI_YEARS.end);
                    } else if (tniMode === "examples" && yearStart === EXAMPLES_TNI_YEARS.start && yearEnd === EXAMPLES_TNI_YEARS.end) {
                      setYearStart(RECENT_YEARS.start);
                      setYearEnd(RECENT_YEARS.end);
                    }
                  }}
                  className="sr-only"
                />
                <span
                  aria-hidden="true"
                  className={cn(
                    "mt-0.5 h-4 w-4 shrink-0 rounded-full border-2 transition-colors duration-150 ease-out-expo",
                    selected ? "border-accent" : "border-border-strong",
                  )}
                  style={selected ? { borderWidth: 5 } : undefined}
                />
                <span className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium">{m.label}</span>
                  <span className="text-xs leading-relaxed text-fg-3">
                    {m.hint}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </Field>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <div className="text-xs text-fg-3">
          总任务数{" "}
          <strong
            className={cn(
              "font-mono text-sm font-semibold tabular-nums",
              overLimit ? "text-red-600 dark:text-red-400" : "text-fg",
            )}
          >
            {totalTasks}
          </strong>
          {overLimit && (
            <span className="ml-2 text-red-600 dark:text-red-400">
              超过上限 100，请缩小范围
            </span>
          )}
        </div>
        <Button
          type="submit"
          variant="primary"
          disabled={!canSubmit}
          aria-live="polite"
        >
          {submitting ? (
            <>
              <Spinner /> 提交中…
            </>
          ) : (
            "提交任务"
          )}
        </Button>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-700 dark:text-red-300"
        >
          {error}
        </div>
      )}
    </form>
  );
}

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <label
          htmlFor={id}
          className="text-sm font-medium focus-visible:outline-none"
        >
          {label}
        </label>
        {hint && (
          <span
            className={cn(
              "text-xs",
              error ? "text-red-600 dark:text-red-400" : "text-fg-3",
            )}
          >
            {error ?? hint}
          </span>
        )}
      </div>
      {children}
      {!hint && error && (
        <div className="text-xs text-red-600 dark:text-red-400">{error}</div>
      )}
    </div>
  );
}

function YearInput({
  id,
  value,
  onChange,
  invalid,
  ariaLabel,
}: {
  id: string;
  value: number;
  onChange(v: number): void;
  invalid: boolean;
  ariaLabel: string;
}) {
  return (
    <input
      id={id}
      name={id}
      type="number"
      inputMode="numeric"
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(parseInt(e.target.value, 10) || value)}
      min={MIN_YEAR}
      max={CURRENT_YEAR + 1}
      className={cn(
        "w-24 rounded-md border bg-surface-2 px-2.5 py-1.5",
        "font-mono text-sm tabular-nums transition-colors duration-150 ease-out-expo",
        "focus:border-accent focus:ring-2 focus:ring-accent/20 focus:outline-none",
        invalid ? "border-red-500/60" : "border-border-strong",
      )}
    />
  );
}

function Spinner() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      className="h-3.5 w-3.5 animate-spin"
    >
      <circle
        cx="8"
        cy="8"
        r="6.5"
        fill="none"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="2.5"
      />
      <path
        d="M14.5 8A6.5 6.5 0 0 0 8 1.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
