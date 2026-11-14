import { describe, expect, it } from "vitest";
import { ModelHubAnnotationQueuesExportJobResponse } from "src/generated/api-contracts/api.zod";

const pending = {
  job_id: "0deae36f-b46e-42fc-a367-284062e14901",
  queue_id: "7aae6733-1a8c-4ede-a2ed-0782dd1fccdd",
  status: "pending",
  export_format: "json",
  item_count: null,
  file_url: null,
  file_name: null,
  error: null,
  created_at: "2026-11-10T00:00:00Z",
  started_at: null,
  finished_at: null,
};

describe("generated export job contract", () => {
  it("accepts the pending wire response with nullable result fields", () => {
    expect(
      ModelHubAnnotationQueuesExportJobResponse.parse({
        status: true,
        result: pending,
      }).result,
    ).toEqual(pending);
  });

  it("accepts a failed job without a file", () => {
    const result = {
      ...pending,
      status: "failed",
      error: "Export timed out",
      finished_at: "2026-11-10T01:00:00Z",
    };
    expect(
      ModelHubAnnotationQueuesExportJobResponse.parse({ result }).result,
    ).toEqual(result);
  });

  it("rejects a string item count rather than silently coercing it", () => {
    expect(
      ModelHubAnnotationQueuesExportJobResponse.safeParse({
        result: { ...pending, item_count: "1001" },
      }).success,
    ).toBe(false);
  });
});
