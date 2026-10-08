// Mirrors the SSE payloads emitted by api/runner._publish on the backend.
// Each payload also carries `seq`, the job-global monotonic id that goes out
// as the SSE `id:` line. The UI reads it from MessageEvent.lastEventId (see
// lib/sse.ts), not from the parsed body, so it is not modelled here.

export type JobStatus =
  | "pending"
  | "running"
  | "done"
  | "error"
  | "cancelled";

export type JobEvent =
  | { type: "status"; status: string }
  | {
      type: "log";
      level: string;
      message: string;
      module: string;
      ts: string;
    }
  | { type: "done"; rows: number; result_path: string | null }
  | { type: "error"; error: string; trace: string }
  | { type: "eof" };

export interface CreateJobRequest {
  company_codes: string[];
  years: number[];
  report_types: string[];
  financial_data_csv?: string | null;
  financial_data_source?: "none" | "akshare";
}

export interface CreateJobResponse {
  job_id: string;
  status: JobStatus;
}

export interface JobSnapshot {
  id: string;
  status: JobStatus;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result_path: string | null;
  history_length: number;
  spec: {
    company_codes: string[];
    years: number[];
    report_types: string[];
  };
}
