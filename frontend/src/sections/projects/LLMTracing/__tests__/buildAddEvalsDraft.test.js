import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { serializeTaskFilterRowForApi } from "src/sections/common/EvalsTasks/task_filter_serialization";

import {
  buildAddEvalsDraft,
  toAddEvalsFormRows,
} from "../buildAddEvalsDraft";

// The draft is the only thing that survives the hop to the create page, so
// whatever preset the toolbar was showing has to travel with the range.
// Dropping it forces the create page to guess, and a day-granular guess
// rewrites the window the user was actually looking at.
const storedDrafts = new Map();
const testStorage = {
  clear: () => storedDrafts.clear(),
  getItem: (key) => storedDrafts.get(key) ?? null,
  setItem: (key, value) => storedDrafts.set(key, String(value)),
};

const draftValues = (url) => {
  const draftId = new URLSearchParams(url.split("?")[1]).get("draft");
  return JSON.parse(testStorage.getItem(`task-draft-${draftId}`)).values;
};

describe("buildAddEvalsDraft time window", () => {
  beforeEach(() => {
    testStorage.clear();
    vi.stubGlobal("localStorage", testStorage);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("carries the toolbar's preset into the draft", () => {
    const url = buildAddEvalsDraft({
      observeId: "proj",
      rowType: "spans",
      dateFilter: {
        dateFilter: ["2026-02-21 00:00:00", "2026-08-21 00:00:00"],
        dateOption: "6M",
      },
    });
    expect(draftValues(url).datePreset).toBe("6M");
  });

  it("keeps a zoomed window as Custom", () => {
    const url = buildAddEvalsDraft({
      observeId: "proj",
      rowType: "spans",
      dateFilter: {
        dateFilter: ["2026-08-21 08:00:00", "2026-08-21 15:00:00"],
        dateOption: "Custom",
      },
    });
    const values = draftValues(url);
    expect(values.datePreset).toBe("Custom");
    expect([values.startDate, values.endDate]).toEqual([
      "2026-08-21 08:00:00",
      "2026-08-21 15:00:00",
    ]);
  });

  it("treats a range with no preset as Custom rather than guessing", () => {
    const url = buildAddEvalsDraft({
      observeId: "proj",
      rowType: "spans",
      dateFilter: {
        dateFilter: ["2026-08-21 08:00:00", "2026-08-21 15:00:00"],
      },
    });
    expect(draftValues(url).datePreset).toBe("Custom");
  });

  // With no incoming window the helper generates a twelve-month range, so the
  // preset has to agree with the range it just built.
  it("labels its own generated fallback range 12M", () => {
    const url = buildAddEvalsDraft({ observeId: "proj", rowType: "spans" });
    expect(draftValues(url).datePreset).toBe("12M");
  });
});

describe("buildAddEvalsDraft property identity", () => {
  it("preserves eval registry identity and the canonical EVAL_METRIC type", () => {
    const [row] = toAddEvalsFormRows([
      {
        column_id: "quality",
        property_id: "eval_config:eval-config-1",
        filter_config: {
          col_type: "EVAL_METRIC",
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: 0.8,
        },
      },
    ]);

    expect(row).toEqual(
      expect.objectContaining({
        property: "quality",
        propertyId: "quality",
        registryId: "eval_config:eval-config-1",
        fieldCategory: "eval",
        apiColType: "EVAL_METRIC",
        filterConfig: expect.objectContaining({
          colType: "EVAL_METRIC",
          filterType: "number",
          filterOp: "greater_than",
          filterValue: 0.8,
        }),
      }),
    );
    expect(serializeTaskFilterRowForApi(row)).toMatchObject({
      column_id: "quality",
      property_id: "eval_config:eval-config-1",
      filter_config: {
        col_type: "EVAL_METRIC",
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 0.8,
      },
    });
  });

  it("keeps same-name system and custom properties distinct in the draft", () => {
    const rows = toAddEvalsFormRows([
      {
        column_id: "model",
        property_id: "system_attribute:traces:model",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "text",
          filter_op: "equals",
          filter_value: "gpt-4.1",
        },
      },
      {
        column_id: "model",
        property_id: "custom_attribute:model",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_type: "text",
          filter_op: "equals",
          filter_value: "customer-model",
        },
      },
    ]);

    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          propertyId: "model",
          registryId: "system_attribute:traces:model",
          apiColType: "SYSTEM_METRIC",
        }),
        expect.objectContaining({
          propertyId: "model",
          registryId: "custom_attribute:model",
          apiColType: "SPAN_ATTRIBUTE",
        }),
      ]),
    );
  });
});
