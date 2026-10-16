import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {}, develop: { eval: {} } },
}));

import {
  draftsFromQuestions,
  endUserIds,
  formatAgo,
  idsTaskPayload,
  limitHelp,
  limitOptionLabel,
  normalizeReads,
  populationCount,
  scopeLabel,
  sessionIdsFromUserSessions,
  splitScopeFilters,
  userSessionsRequest,
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
const ITEMS = [
  { configId: "c1", question: "Did the customer get what they came for?" },
  { configId: "c2", question: "Was the customer upset?" },
];
const U1 = "5fd6ed2b-9f0c-577b-99fd-63f543ca3f20";
const U2 = "d6126f89-131e-59fd-a9ac-9a1448c03c0c";

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

describe("task payloads", () => {
  const payload = (unit, ids) =>
    idsTaskPayload({ projectId: "p1", items: ITEMS, unit, ids });

  it.each([
    ["traces", "traces", "trace_id"],
    ["spans", "spans", "span_id"],
    ["sessions", "sessions", "session_id"],
    // A user question is answered session by session.
    ["users", "sessions", "session_id"],
  ])("names the %s it asks about, and nothing else", (unit, rowType, key) => {
    expect(payload(unit, ["a", "b", "c"])).toEqual({
      name: "Instant eval · Did the customer get what they came for?",
      project: "p1",
      evals: ["c1", "c2"],
      filters: { [key]: ["a", "b", "c"] },
      run_type: "historical",
      row_type: rowType,
      spans_limit: 3,
      sampling_rate: 100,
    });
  });
});

describe("the users flow", () => {
  it("keeps only end-user ids the lookup accepts, once each", () => {
    expect(
      endUserIds([{ id: U1 }, { id: "cust_kai" }, U2, { id: U1 }, null]),
    ).toEqual([U1, U2]);
  });

  it("asks for each user's latest five sessions in the view's window", () => {
    expect(
      userSessionsRequest({
        projectId: "p1",
        userIds: [U1, U2],
        filters: [STATUS_ITEM, DATE_ITEM],
      }),
    ).toEqual({
      project_id: "p1",
      end_user_ids: [U1, U2],
      per_user: 5,
      window: RANGE,
    });
    expect(
      userSessionsRequest({ projectId: "p1", userIds: [U1], filters: [] }),
    ).not.toHaveProperty("window");
  });

  it("collects the users' session ids in order, once each", () => {
    expect(
      sessionIdsFromUserSessions({
        sessions: [
          { session_id: "s2", end_user_id: U1 },
          { session_id: "s1", end_user_id: U1 },
          { session_id: "s2", end_user_id: U2 },
          { end_user_id: U2 },
        ],
        users: 2,
      }),
    ).toEqual(["s2", "s1"]);
    expect(sessionIdsFromUserSessions(undefined)).toEqual([]);
  });

  it("says that each user is read through their latest sessions", () => {
    expect(limitOptionLabel("users", 25)).toBe("Up to 25 users × 5 sessions");
    expect(limitHelp({ unit: "users", limit: 25 })).toMatch(
      /latest 5 sessions .*up to 125 sessions/,
    );
    expect(limitOptionLabel("sessions", 50)).toBe("Up to 50 sessions");
  });
});

describe("reads", () => {
  it("keeps a read only on the views that offer it", () => {
    expect(normalizeReads("sessions", "input_output")).toBe("transcript");
    expect(normalizeReads("users", undefined)).toBe("transcript");
    expect(normalizeReads("users", "transcript_full")).toBe("transcript_full");
    expect(normalizeReads("traces", "output")).toBe("output");
    expect(normalizeReads("traces", "trace_tree")).toBe("trace_tree");
    expect(normalizeReads("traces", "transcript")).toBe("input_output");
    // A span is read on its own; the trace outline is for traces.
    expect(normalizeReads("spans", "trace_tree")).toBe("input_output");
    expect(normalizeReads("spans", "input")).toBe("input");
  });
});

describe("scope", () => {
  it("labels the scope with its date label and other filters", () => {
    expect(
      scopeLabel({ label: "Past 7D", filters: [DATE_ITEM, STATUS_ITEM] }),
    ).toBe("Past 7D · 1 filter");
    expect(scopeLabel({ label: "Past 7D", filters: [DATE_ITEM] })).toBe(
      "Past 7D",
    );
  });

  it("only proves an exact count when the list does", () => {
    expect(populationCount({ items: [{}, {}], total: 2, limit: 200 })).toEqual({
      count: 2,
      lowerBound: false,
    });
    // Filled the read and the list gave no larger total: at least that many.
    expect(
      populationCount({
        items: new Array(200).fill({}),
        total: 200,
        limit: 200,
      }),
    ).toEqual({ count: 200, lowerBound: true });
    expect(
      populationCount({
        items: new Array(200).fill({}),
        total: 1234,
        limit: 200,
      }),
    ).toEqual({ count: 1234, lowerBound: false });
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
