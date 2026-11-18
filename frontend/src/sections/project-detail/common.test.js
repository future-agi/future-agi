import { describe, expect, it } from "vitest";

import {
  normalizeRunListColumnConfig,
  serializeRunListFilters,
} from "./common";

describe("serializeRunListFilters", () => {
  it("drops empty UI placeholder filters and strips UI-only ids", () => {
    expect(
      serializeRunListFilters([
        {
          id: "placeholder",
          column_id: "",
          filter_config: {
            filter_type: "",
            filter_op: "",
            filter_value: "",
          },
        },
        {
          id: "active-filter",
          column_id: "run_name",
          display_name: "Run Name",
          filter_config: {
            filter_type: "text",
            filter_op: "contains",
            filter_value: "DEFAULT",
          },
        },
      ]),
    ).toEqual([
      {
        column_id: "run_name",
        display_name: "Run Name",
        filter_config: {
          filter_type: "text",
          filter_op: "contains",
          filter_value: "DEFAULT",
        },
      },
    ]);
  });

  it("preserves canonical optional filter fields only", () => {
    expect(
      serializeRunListFilters([
        {
          id: "filter-id",
          column_id: "avg_latency",
          source: "prototype",
          output_type: "number",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: 10,
            col_type: "SYSTEM_METRIC",
            transient_label: "Latency",
          },
        },
      ]),
    ).toEqual([
      {
        column_id: "avg_latency",
        source: "prototype",
        output_type: "number",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: 10,
          col_type: "SYSTEM_METRIC",
        },
      },
    ]);
  });
});

describe("normalizeRunListColumnConfig", () => {
  it("normalizes snake_case API column config into grid camelCase fields", () => {
    expect(
      normalizeRunListColumnConfig(
        {
          id: "avg_latency",
          name: "Avg. Latency",
          is_visible: true,
          group_by: "System Metrics",
          output_type: "number",
          reverse_output: false,
          annotation_label_type: "numeric",
          choices_map: { low: "Low" },
          eval_template_id: "eval-template-id",
          source_field: "reason",
          parent_eval_id: "eval-id",
        },
        { avg_latency_ms: 42 },
      ),
    ).toMatchObject({
      id: "avg_latency",
      isVisible: true,
      groupBy: "System Metrics",
      outputType: "number",
      reverseOutput: false,
      annotationLabelType: "numeric",
      choicesMap: { low: "Low" },
      evalTemplateId: "eval-template-id",
      sourceField: "reason",
      parentEvalId: "eval-id",
      value: 42,
    });
  });
});
