import { describe, expect, it } from "vitest";

import {
  buildTracingPreviewListParams,
  buildTracingVoicePreviewListParams,
} from "./TracingTestMode";
import { mergeTracingFieldNames } from "./useExactEvalAttributeFields";

describe("buildTracingPreviewListParams", () => {
  it("does not send unsupported interval params to observe list endpoints", () => {
    const params = buildTracingPreviewListParams({
      selectedProjectId: "project-1",
      effectiveFilters: [
        {
          column_id: "created_at",
          filter_config: {
            filter_type: "datetime",
            filter_op: "between",
            filter_value: [
              "2025-01-01T00:00:00.000Z",
              "2026-01-01T00:00:00.000Z",
            ],
          },
        },
      ],
    });

    expect(params).toEqual({
      project_id: "project-1",
      page_number: 0,
      page_size: 50,
      cursor_mode: true,
      filters: JSON.stringify([
        {
          column_id: "created_at",
          filter_config: {
            filter_type: "datetime",
            filter_op: "between",
            filter_value: [
              "2025-01-01T00:00:00.000Z",
              "2026-01-01T00:00:00.000Z",
            ],
          },
        },
      ]),
    });
    expect(params).not.toHaveProperty("interval");
    expect(params).not.toHaveProperty("allow_sampled");
  });

  it("does not opt filtered voice previews into partial first pages", () => {
    const params = buildTracingVoicePreviewListParams({
      selectedProjectId: "project-1",
      effectiveFilters: [{ column_id: "call_status" }],
    });

    expect(params).toMatchObject({
      project_id: "project-1",
      page: 1,
      page_size: 50,
      cursor_mode: true,
    });
    expect(params).not.toHaveProperty("allow_sampled");
  });

  it("merges an exact rare field omitted by the preview-row catalog", () => {
    const fields = mergeTracingFieldNames(
      ["input", "output"],
      ["final_status", "input"],
    );

    expect(fields).toEqual(["input", "output", "final_status"]);
  });
});
