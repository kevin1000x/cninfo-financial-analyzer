export type FinancialDataMode = "skip" | "examples" | "akshare";

export function financialDataInput(mode: FinancialDataMode): {
  financial_data_csv: string | null;
  financial_data_source: "none" | "akshare";
} {
  if (mode === "akshare") {
    return { financial_data_csv: null, financial_data_source: "akshare" };
  }
  if (mode === "examples") {
    return {
      financial_data_csv: "examples/financial_data.csv",
      financial_data_source: "none",
    };
  }
  return { financial_data_csv: null, financial_data_source: "none" };
}
