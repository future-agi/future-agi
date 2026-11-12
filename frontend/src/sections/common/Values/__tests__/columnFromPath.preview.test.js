// L2 fix round 1: Column from path on the real backend. Dev's
// preview/extract_json/ is strict (PreviewDatasetOperationRequest has no
// new_column_name) and answers {preview_results: [{row_id, input, output}],
// sample_size}; the dialog's summary reads both that and the prototype's
// richer answer.
import { describe, expect, it } from "vitest";
import { unknownFields } from "src/api/contracts/strictBody";
import { extractBody } from "../ColumnFromPathDialog";
import { previewSummary } from "../columnFromPathPreview";

describe("Column from path preview", () => {
  it("sends only fields dev's strict preview route declares", () => {
    const body = extractBody("c1", "payload", "payload.city");
    expect(body).toEqual({ column_id: "c1", json_key: "city" });
    expect(unknownFields(body, "PreviewDatasetOperationRequest")).toEqual([]);
    // What the dialog used to add: dev answers 400 "Unknown field.".
    expect(
      unknownFields(
        { ...body, new_column_name: "payload.city" },
        "PreviewDatasetOperationRequest",
      ),
    ).toEqual(["new_column_name"]);
    // The name travels with Add column, which declares it.
    expect(
      unknownFields(
        { ...body, new_column_name: "city", concurrency: 1 },
        "ExtractJsonColumnRequest",
      ),
    ).toEqual([]);
  });

  it("summarises dev's answer (first rows, null = not found)", () => {
    const s = previewSummary({
      message: "Preview completed successfully",
      preview_results: [
        { row_id: "r1", input: "{'city': 'Lyon'}", output: "Lyon" },
        { row_id: "r2", input: "{'x': 1}", output: null },
        { row_id: "r3", input: "{'city': {'n': 1}}", output: { n: 1 } },
      ],
      sample_size: 3,
    });
    expect(s.found).toBe(2);
    expect(s.total).toBe(3);
    expect(s.label).toBe("Found in 2 of the first 3 rows");
    expect(s.rows.map((r) => r.found)).toEqual([true, false, true]);
    expect(s.dataType).toBeNull();
  });

  it("keeps the prototype's counts and type", () => {
    const s = previewSummary({
      found: 31,
      total: 1200,
      data_type: "text",
      preview_results: [{ row_id: "r1", output: "a", found: true }],
    });
    expect(s.label).toBe("Found in 31 of 1,200 rows");
    expect(s.dataType).toBe("text");
  });

  it("is null before an answer", () => {
    expect(previewSummary(undefined)).toBeNull();
  });
});
