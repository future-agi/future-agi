import { describe, expect, it } from "vitest";
import { batchResultsExport } from "./batchResultsExport";

const batch = {
  batch_id: "job-1", status: "completed", total: 3,
  summary: { completed: 1, failed: 1, total_cost: null, cost_known: false, unpriced_requests: 1, total_input_tokens: 7 },
  results: [
    { index: 0, status: "success", cost: 0.00005, cost_known: true, input_tokens: 7, response: { choices: [{ message: { content: "Line one\nLine two" } }] } },
    { index: 2, status: "error", cost: null, cost_known: false, error: "Provider unavailable" },
  ],
};

describe("R3-CONTROLS-07 portable loaded batch results", () => {
  it("preserves the full returned payload and never claims missing results are complete", () => {
    const snapshot = JSON.parse(batchResultsExport(batch, "job-1", "json"));
    expect(snapshot).toMatchObject(batch);
    expect(snapshot.export_scope).toBe("loaded-results-only");
    expect(snapshot.snapshot_kind).toBe("terminal");
    expect(snapshot.results).toHaveLength(2);
    expect(snapshot).not.toHaveProperty("complete");
  });
  it("exports JSONL metadata followed by intact result records, including multiline responses", () => {
    const [metadata, ...results] = batchResultsExport(batch, "job-1", "jsonl").trim().split("\n").map((line) => JSON.parse(line));
    expect(metadata).toMatchObject({ record_type: "batch_snapshot", batch_id: "job-1", summary: batch.summary });
    expect(results).toEqual(batch.results);
  });
  it.each(["pending", "processing", undefined])("labels %s as a partial snapshot", (status) => {
    expect(JSON.parse(batchResultsExport({ ...batch, status }, "job-1", "json")).snapshot_kind).toBe("partial");
  });
  it("does not export an exact charge without completeness evidence", () => {
    const old = { ...batch, summary: { total_cost: 0 }, results: [{ index: 0, cost: 0 }] };
    const exported = JSON.parse(batchResultsExport(old, "job-1", "json"));
    expect(exported.summary).toMatchObject({ total_cost: null, cost_known: false });
    expect(exported.results[0]).toMatchObject({ cost: null, cost_known: false });
  });
  it("does not invent empty results when the server omitted them", () => {
    const { results, ...missing } = batch;
    expect(batchResultsExport(missing, "job-1", "json")).toBeNull();
    expect(batchResultsExport({ ...missing, results: [] }, "job-1", "json")).toBeNull();
  });
});
