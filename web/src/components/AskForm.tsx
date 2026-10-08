import { useId, useRef, useState } from "react";
import { Button } from "@/components/ui";

interface Props {
  value: string;
  onChange(v: string): void;
  onSubmit(q: string): void;
  busy: boolean;
  /** Server-side cap, mirrored here so the counter matches what gets rejected. */
  maxBytes: number;
  /*
   * Copy is a prop, not a constant, because this form now serves an entry that
   * takes either a question or a pasted conclusion (`D-041`). Hard-coding
   * "问一个问题" there would tell the reader the box does less than it does.
   */
  label?: string;
  placeholder?: string;
  submitLabel?: string;
  busyLabel?: string;
  emptyHint?: string;
  hint?: string;
  rows?: number;
}

const encoder = new TextEncoder();

export function AskForm({
  value,
  onChange,
  onSubmit,
  busy,
  maxBytes,
  label = "问一个问题",
  placeholder = "例如：600519 2023 年的资产负债率是多少…",
  submitLabel = "提问",
  busyLabel = "正在算…",
  emptyHint = "先写一个问题，再点提问。",
  hint = "只接受一个问题字符串 —— 没有文件上传，不收你自己的财务数据",
  rows = 2,
}: Props) {
  const id = useId();
  const [attempted, setAttempted] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  // Bytes, not characters: the server's limit is on UTF-8 bytes and a Chinese
  // question is 3 bytes per character. A character counter would tell the
  // reader they have room when they don't.
  const bytes = encoder.encode(value).length;
  const tooLong = bytes > maxBytes;
  const empty = value.trim() === "";
  const invalid = tooLong || (attempted && empty);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setAttempted(true);
    if (empty || tooLong || busy) {
      // The submit button stays enabled and this refuses instead (DESIGN.md
      // § 状态清单). A disabled button gives no reason: you click, nothing
      // happens, and there is nothing to read. Focusing the field puts the
      // cursor where the fix is; the hint says what is wrong.
      ref.current?.focus();
      return;
    }
    onSubmit(value);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2.5">
      <label
        htmlFor={id}
        className="text-xs font-medium tracking-widest text-fg-4 uppercase"
      >
        {label}
      </label>

      <textarea
        id={id}
        ref={ref}
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={busy}
        aria-invalid={invalid}
        aria-describedby={`${id}-hint`}
        placeholder={placeholder}
        className={`w-full resize-y rounded-md border bg-surface-2 px-3 py-2.5 font-mono text-[13px] leading-relaxed text-fg transition-[border-color] duration-150 ease-out-expo placeholder:text-fg-4 focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:outline-none disabled:opacity-60 ${
          invalid ? "border-red-500/50" : "border-border-strong"
        }`}
      />

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? busyLabel : submitLabel}
        </Button>

        <span
          id={`${id}-hint`}
          aria-live="polite"
          className={`text-xs ${invalid ? "text-red-600 dark:text-red-400" : "text-fg-3"}`}
        >
          {tooLong
            ? `太长了：${bytes} / ${maxBytes} 字节。删掉一些再提交。`
            : attempted && empty
              ? emptyHint
              : hint}
        </span>
      </div>
    </form>
  );
}
