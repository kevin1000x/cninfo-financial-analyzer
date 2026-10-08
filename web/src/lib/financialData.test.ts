import { describe, expect, it } from "vitest";
import { financialDataInput } from "./financialData";

describe("financialDataInput", () => {
  it("selects AKShare without exposing a CSV path", () => {
    expect(financialDataInput("akshare")).toEqual({
      financial_data_csv: null,
      financial_data_source: "akshare",
    });
  });

  it("preserves the example CSV as an explicit local source", () => {
    expect(financialDataInput("examples")).toEqual({
      financial_data_csv: "examples/financial_data.csv",
      financial_data_source: "none",
    });
  });
});
