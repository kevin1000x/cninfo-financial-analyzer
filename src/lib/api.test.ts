import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, cancelJob } from "./api";

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("cancelJob", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs to the cancel route and returns the acknowledgement", async () => {
    const fetchMock = vi.fn(
      async () => jsonResponse({ job_id: "abc", cancel_requested: true }, 202),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(cancelJob("abc123")).resolves.toEqual({
      job_id: "abc",
      cancel_requested: true,
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/proxy/jobs/abc123/cancel", {
      method: "POST",
    });
  });

  it("surfaces a 409 refusal as an ApiError carrying the status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ detail: "job already finished: status=done" }, 409),
      ),
    );

    const err = await cancelJob("abc123").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toHaveProperty("status", 409);
    expect(err).toHaveProperty("detail", {
      detail: "job already finished: status=done",
    });
  });
});
