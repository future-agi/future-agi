import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {}, develop: { eval: {} } },
}));

import {
  draftsFromQuestions,
  formatAgo,
  normalizeReads,
  splitScopeFilters,
} from "../instantEvalUnits";

const RANGE = ["2026-10-08T00:00:00Z", "2026-10-15T23:00:00Z"];
const DATE_ITEM = {
  column_id: "created_at",
  filter_config: {
    filter_type: "datetime",
    filter_op: "between",
    filter_value: RANGE,
  },
};
const STATUS_ITEM = {
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "OK",
  },
};

describe("splitScopeFilters", () => {
  it("moves the grid's created_at window into date_range", () => {
    expect(splitScopeFilters([STATUS_ITEM, DATE_ITEM])).toEqual({
      dateRange: RANGE,
      filters: [STATUS_ITEM],
    });
  });

  it("leaves non-between created_at filters in place", () => {
    const after = {
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "greater_than",
        filter_value: RANGE[0],
      },
    };
    expect(splitScopeFilters([after])).toEqual({
      dateRange: undefined,
      filters: [after],
    });
  });
});

describe("reads", () => {
  it("keeps a read only on the views that offer it", () => {
    expect(normalizeReads("sessions", "input_output")).toBe("transcript_full");
    expect(normalizeReads("users", undefined)).toBe("transcript_full");
    expect(normalizeReads("sessions", "transcript")).toBe("transcript");
    expect(normalizeReads("users", "transcript_full")).toBe("transcript_full");
    expect(normalizeReads("traces", "output")).toBe("output");
    expect(normalizeReads("traces", "trace_tree")).toBe("trace_tree");
    expect(normalizeReads("traces", "transcript")).toBe("input_output");
    // A span is read on its own; the trace outline is for traces.
    expect(normalizeReads("spans", "trace_tree")).toBe("input_output");
    expect(normalizeReads("spans", "input")).toBe("input");
  });
});

describe("drafts", () => {
  it("turns handed-over questions into editor drafts", () => {
    const drafts = draftsFromQuestions([
      { question: "Did they get it?", answerType: "yes_no" },
      {
        question: "How did it end?",
        answerType: "category",
        categories: ["Resolved", " resolved", "Abandoned"],
        definitions: { Resolved: "The customer said thanks" },
      },
    ]);
    expect(drafts).toEqual([
      {
        question: "Did they get it?",
        answerType: "yes_no",
        categories: [],
        definitions: {},
      },
      {
        question: "How did it end?",
        answerType: "category",
        categories: ["Resolved", "Abandoned"],
        definitions: { Resolved: "The customer said thanks" },
      },
    ]);
    expect(draftsFromQuestions(undefined)).toHaveLength(1);
    expect(
      draftsFromQuestions(
        Array.from({ length: 7 }, (_, i) => ({ question: `q${i}` })),
      ),
    ).toHaveLength(5);
  });
});

describe("time", () => {
  it("says how long ago a run was", () => {
    const now = new Date("2026-10-15T12:00:00Z").getTime();
    expect(formatAgo("2026-10-15T11:59:40Z", now)).toBe("just now");
    expect(formatAgo("2026-10-15T11:56:00Z", now)).toBe("4 min ago");
    expect(formatAgo("2026-10-15T09:00:00Z", now)).toBe("3 h ago");
    expect(formatAgo("2026-10-14T10:00:00Z", now)).toBe("yesterday");
  });
});
