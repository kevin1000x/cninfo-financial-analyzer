import { useEffect, useId, useState } from "react";
import { searchStocks, type Stock, type StockSearch } from "@/lib/stocks";
import { cn } from "@/lib/utils";

export function StockPicker({ selected, onChange }: { selected: Stock[]; onChange(stocks: Stock[]): void }) {
  const listId = useId();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [result, setResult] = useState<StockSearch | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      void searchStocks(query, ctl.signal).then(data => {
        if (ctl.signal.aborted) return;
        setResult(data); setError(null); setActive(0);
      }).catch(err => {
        if (!ctl.signal.aborted) { setError(err instanceof Error ? err.message : "目录加载失败"); setResult(null); }
      }).finally(() => { if (!ctl.signal.aborted) setLoading(false); });
    }, 200);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [query, open]);

  function pick(stock: Stock) {
    if (stock.supported === false) return;
    if (!selected.some(item => item.code === stock.code)) onChange([...selected, stock]);
    setQuery(""); setOpen(false); setActive(0);
  }
  const items = result?.items ?? [];

  return <div className="flex flex-col gap-3">
    <div className="flex items-baseline justify-between gap-2"><label htmlFor={listId + "-input"} className="text-sm font-medium">选择公司</label><span className="text-xs text-fg-3">名称 / 代码 / 拼音</span></div>
    <div className="relative" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
      <div className="flex items-center gap-2 rounded-md border border-border-strong bg-surface-2 px-3 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20">
        <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4 shrink-0 fill-none stroke-fg-3" strokeWidth="1.6"><circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" /></svg>
        <input id={listId + "-input"} type="search" role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={listId} aria-activedescendant={open && items[active] ? `${listId}-${active}` : undefined} autoComplete="off" value={query} placeholder="搜索公司，如「茅台」或「600519」" className="h-12 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-4" onFocus={() => setOpen(true)} onChange={e => { setQuery(e.target.value); setOpen(true); setLoading(true); setResult(null); setError(null); setActive(0); }} onKeyDown={e => {
          if (e.nativeEvent.isComposing || e.keyCode === 229) return;
          if (e.key === "Escape") { setOpen(false); return; }
          if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setOpen(true); setActive(i => Math.max(0, Math.min(items.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))); }
          if (e.key === "Enter") { e.preventDefault(); if (open && items[active]) pick(items[active]); }
        }} />
      </div>
      {open && <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-border-strong bg-surface shadow-lg">
        <div className="border-b border-border px-3 py-2 text-[11px] text-fg-3" role="status">{loading ? "正在查找公司…" : error ?? (result ? `找到 ${result.total} 家公司${result.total > items.length ? ` · 显示前 ${items.length} 家` : ""}` : "输入名称、代码或拼音搜索")}</div>
        <ul id={listId} role="listbox" aria-label="公司搜索结果" className="max-h-64 overflow-y-auto p-1">
          {!loading && !error && result && !items.length && <li role="presentation" className="px-3 py-5 text-center text-sm text-fg-3">没有找到公司，请尝试名称的一部分或完整代码。</li>}
          {items.map((stock, index) => {
            const chosen = selected.some(s => s.code === stock.code);
            return <li key={stock.code} id={`${listId}-${index}`} role="option" aria-selected={chosen}>
              <button type="button" disabled={stock.supported === false} className={cn("flex min-h-11 w-full items-center gap-3 rounded px-3 py-2 text-left text-sm focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50", index === active ? "bg-accent-soft" : "hover:bg-surface-2")} onMouseDown={e => e.preventDefault()} onClick={() => pick(stock)}>
                <span className="font-mono text-xs text-fg-3">{stock.code}</span><span className="min-w-0 flex-1 truncate font-medium">{stock.name}</span><span className="text-[11px] text-fg-3">{stock.market}{stock.supported === false ? " · 暂不支持" : ""}</span>{chosen && <span aria-label="已选择" className="text-accent">✓</span>}
              </button>
            </li>;
          })}
        </ul>
        {result?.stale && <p className="border-t border-border px-3 py-2 text-[11px] text-amber-700 dark:text-amber-400">正在使用缓存目录，最新公司信息可能尚未同步。</p>}
      </div>}
    </div>
    {selected.length > 0 && <ul aria-label="已选公司" className="flex flex-wrap gap-2">{selected.map(stock => <li key={stock.code} className="flex min-h-9 items-center gap-2 rounded-md border border-accent/20 bg-accent-soft pl-3 text-xs"><span className="font-medium">{stock.name}</span><span className="font-mono text-fg-3">{stock.code}</span><button type="button" aria-label={`移除 ${stock.name}`} onClick={() => onChange(selected.filter(s => s.code !== stock.code))} className="grid h-9 w-9 place-items-center rounded-r-md text-fg-3 hover:bg-accent/10 hover:text-fg focus-visible:outline-accent">×</button></li>)}</ul>}
    <p className="text-[11px] leading-relaxed text-fg-3">公司目录用于提交年报分析。结论核查的可用公司与年份，请查看核查页的覆盖范围。</p>
  </div>;
}
