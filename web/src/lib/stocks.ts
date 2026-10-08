export interface Stock { code: string; name: string; market: string; pinyin?: string; supported?: boolean }
export interface StockSearch { items: Stock[]; total: number; updated_at: string | null; stale: boolean }

export async function searchStocks(query: string, signal?: AbortSignal): Promise<StockSearch> {
  const response = await fetch(`/api/proxy/stocks?q=${encodeURIComponent(query.trim())}&limit=20`, { signal });
  if (!response.ok) throw new Error("股票目录暂不可用，可先使用批量代码粘贴。");
  return response.json() as Promise<StockSearch>;
}

export function parseStockCodes(raw: string): { valid: string[]; invalid: string[] } {
  const tokens = raw.split(/[\s,，;；、]+/).filter(Boolean);
  return { valid: [...new Set(tokens.filter(t => /^\d{6}$/.test(t)))], invalid: tokens.filter(t => !/^\d{6}$/.test(t)) };
}
