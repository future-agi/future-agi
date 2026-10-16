import { describe, expect, it } from "vitest";
import { askLink, readHandoff } from "src/sections/projects/observeHandoff";
import { OBSERVE_LINK_FILTER_PARAM } from "src/sections/projects/LLMTracing/common";
import { ERROR_ANSWER } from "../../instantEvalAnswers";
import { isAnswered, mergeRecords, toRecord, windowOf } from "../useAskRun";
import { visibleRows } from "../askRows";
import { rowsGridLink, sourceViewLink, tabForUnit } from "../askLinks";
import { dayGroup, facetLine, scopeWords } from "../askFormat";
import { autoLimit } from "../askPopulation";
import { lastWeekScope } from "../askScope";

const C1 = "config-1";
const C2 = "config-2";

const row = (id, time, answers = {}, extra = {}) => ({
  id,
  time,
  user_id: `user-${id}`,
  label: `Session ${id}`,
  excerpt: `Where is order ${id}?`,
  answers,
  ...extra,
});

const record = (id, time, answers = {}) =>
  toRecord(row(id, time, answers), "sessions");

describe("an answers row as a record", () => {
  it("keeps the facets and each answer, in-flight answers marked", () => {
    const r = toRecord(
      row(
        "s1",
        "2026-10-13T14:10:00Z",
        {
          [C1]: {
            answer: "No",
            confidence: 0.9,
            explanation: " Never fixed. ",
          },
          [C2]: { answer: null, status: "running" },
        },
        {
          facets: {
            model: "gpt-4o-mini",
            turns: 1,
            duration_ms: 2100,
            error: true,
          },
        },
      ),
      "sessions",
    );
    expect(r).toMatchObject({
      id: "s1",
      unit: "sessions",
      user_id: "user-s1",
      excerpt: "Where is order s1?",
      facets: {
        model: "gpt-4o-mini",
        turns: 1,
        duration_ms: 2100,
        error: true,
      },
    });
    expect(r.answers[C1]).toEqual({
      answer: "No",
      confidence: 0.9,
      explanation: "Never fixed.",
      status: "completed",
    });
    expect(r.answers[C2]).toMatchObject({ answer: null, status: "running" });
    // No facets from an older backend: every facet is unknown, not zero.
    expect(record("s2", null).facets).toEqual({
      model: null,
      turns: null,
      duration_ms: null,
      error: null,
    });
  });

  it("counts a row answered once every question has a settled answer", () => {
    const done = record("a", null, {
      [C1]: { answer: "Yes" },
      [C2]: { answer: ERROR_ANSWER, status: "failed" },
    });
    const reading = record("b", null, {
      [C1]: { answer: "Yes" },
      [C2]: { answer: null, status: "pending" },
    });
    const halfRead = record("c", null, { [C1]: { answer: "Yes" } });
    expect(isAnswered(done, [C1, C2])).toBe(true);
    expect(isAnswered(reading, [C1, C2])).toBe(false);
    expect(isAnswered(halfRead, [C1, C2])).toBe(false);
    expect(isAnswered(done, [])).toBe(false);
  });
});

describe("every row from the start", () => {
  it("shows each handed-over row as not read yet until its answers land", () => {
    const rows = [
      record("s2", "2026-10-13T15:00:00Z", { [C1]: { answer: "No" } }),
    ];
    const seeds = [
      { id: "s1", time: "2026-10-12T09:00:00Z", user_id: "u1" },
      { id: "s2", time: "2026-10-13T15:00:00Z", user_id: "u2" },
      { id: "s3", time: "2026-10-14T10:00:00Z", user_id: "u3" },
    ];
    const merged = mergeRecords(rows, seeds, "sessions");
    // Newest first, no row twice, the answered one as the endpoint sent it.
    expect(merged.map((item) => item.id)).toEqual(["s3", "s2", "s1"]);
    expect(merged[1].answers[C1].answer).toBe("No");
    expect(merged[0]).toMatchObject({ user_id: "u3", answers: {} });
    expect(windowOf(merged)).toEqual([
      "2026-10-12T09:00:00.000Z",
      "2026-10-14T10:00:00.000Z",
    ]);
    expect(windowOf([record("x", null)])).toBeNull();
  });
});

describe("focus and selection narrow the rows", () => {
  const records = [
    record("a", "2026-10-13T14:00:00Z", { [C1]: { answer: "No" } }),
    record("b", "2026-10-13T15:00:00Z", { [C1]: { answer: "Yes" } }),
    record("c", "2026-10-13T16:00:00Z", { [C1]: { answer: "No" } }),
    record("d", "2026-10-13T17:00:00Z"),
  ];
  const ids = (rows) => rows.map((item) => item.id);

  it("shows every row with nothing focused or selected", () => {
    expect(ids(visibleRows(records, {}))).toEqual(["a", "b", "c", "d"]);
    expect(
      ids(visibleRows(records, { focus: { configId: C1, answer: null } })),
    ).toEqual(["a", "b", "c", "d"]);
  });

  it("shows the rows with the focused answer", () => {
    expect(
      ids(visibleRows(records, { focus: { configId: C1, answer: "No" } })),
    ).toEqual(["a", "c"]);
  });

  it("lets a selection on a chart win over the focused answer", () => {
    expect(
      ids(
        visibleRows(records, {
          focus: { configId: C1, answer: "No" },
          selection: { ids: ["b", "d"], label: "Oct 13, 3–5 PM" },
        }),
      ),
    ).toEqual(["b", "d"]);
  });
});

describe("links keep the tab bar on the view the rows came from", () => {
  it("opens the Ask page with the hand-off and the source tab", () => {
    const link = askLink("p1", {
      unit: "sessions",
      label: "Past 7D",
      filters: [{ column_id: "status" }],
    });
    const url = new URL(link, "http://app.test");
    expect(url.pathname).toBe("/dashboard/observe/p1/ask");
    expect(url.searchParams.get("new")).toBe("1");
    expect(url.searchParams.get("unit")).toBe("sessions");
    expect(url.searchParams.get("tab")).toBe("sessions");
    expect(readHandoff(url.searchParams.get("seed"))).toMatchObject({
      unit: "sessions",
      label: "Past 7D",
    });
    const spans = new URL(askLink("p1", { unit: "spans" }), "http://app.test");
    expect(spans.searchParams.get("tab")).toBe("spans");
  });

  it("goes back to each source view with its tab", () => {
    expect(sourceViewLink("p1", "sessions")).toBe(
      "/dashboard/observe/p1/sessions?tab=sessions",
    );
    expect(sourceViewLink("p1", "users")).toBe(
      "/dashboard/observe/p1/users?tab=users",
    );
    expect(sourceViewLink("p1", "spans")).toContain("tab=spans");
    expect(sourceViewLink("p1", "traces")).toContain("tab=traces");
    expect(tabForUnit("sessions")).toBe("sessions");
    expect(tabForUnit("nonsense")).toBe("traces");
  });

  it("filters the Traces list to the chosen rows", () => {
    const rows = [
      record("s1", "2026-10-13T14:00:00Z"),
      record("s2", "2026-10-13T15:00:00Z"),
    ];
    const url = new URL(
      rowsGridLink("p1", "sessions", rows),
      "http://app.test",
    );
    expect(url.pathname).toBe("/dashboard/observe/p1/llm-tracing");
    expect(url.searchParams.get("tab")).toBe("traces");
    const [filter] = JSON.parse(
      url.searchParams.get(OBSERVE_LINK_FILTER_PARAM),
    );
    expect(filter).toMatchObject({
      column_id: "session_id",
      filter_config: { filter_op: "in", filter_value: ["s1", "s2"] },
    });
    // The list's own default window may not reach back to these rows.
    expect(url.searchParams.get("primaryTraceDateFilter")).toContain("Custom");

    const spans = [
      { ...record("span-1", "2026-10-13T14:00:00Z"), trace_id: "t1" },
      { ...record("span-2", "2026-10-13T14:01:00Z"), trace_id: "t1" },
    ];
    const spanUrl = new URL(
      rowsGridLink("p1", "spans", spans),
      "http://app.test",
    );
    const [spanFilter] = JSON.parse(
      spanUrl.searchParams.get(OBSERVE_LINK_FILTER_PARAM),
    );
    expect(spanFilter.column_id).toBe("trace_id");
    expect(spanFilter.filter_config.filter_value).toEqual(["t1"]);
    // Users have no list filter by id.
    expect(rowsGridLink("p1", "users", rows)).toBeNull();
    expect(rowsGridLink("p1", "sessions", [])).toBeNull();
  });
});

describe("the page's words", () => {
  it("spells out the views' short date labels", () => {
    expect(scopeWords("Past 7D")).toBe("Past 7 days");
    expect(scopeWords("Past 7D · 2 filters")).toBe("Past 7 days · 2 filters");
    expect(scopeWords("Past 3M")).toBe("Past 3 months");
    expect(scopeWords("Past 6 hrs")).toBe("Past 6 hours");
    expect(scopeWords("Past 30 mins")).toBe("Past 30 minutes");
    expect(scopeWords("Today")).toBe("Today");
    expect(scopeWords(undefined)).toBe("");
  });

  it("sums up a row's facets", () => {
    expect(
      facetLine({
        model: "gpt-4o-mini",
        turns: 3,
        duration_ms: 148000,
        error: false,
      }),
    ).toBe("gpt-4o-mini · 3 turns · 2 min 28 s");
    expect(
      facetLine({ model: null, turns: 1, duration_ms: null, error: true }),
    ).toBe("1 turn · errored");
    expect(facetLine()).toBe("");
  });

  it("groups History by day", () => {
    const now = new Date(2026, 9, 15, 12);
    expect(dayGroup(new Date(2026, 9, 15, 9), now)).toBe("Today");
    expect(dayGroup(new Date(2026, 9, 14, 9), now)).toBe("Yesterday");
    expect(dayGroup(new Date(2026, 9, 11, 9), now)).toBe("Earlier this week");
    expect(dayGroup(new Date(2026, 8, 1), now)).toBe("Older");
    expect(dayGroup("not a date", now)).toBe("Older");
  });
});

describe("a new question's scope", () => {
  it("takes every row in view when a run size covers them", () => {
    expect(autoLimit([50, 100, 200, 500], 108, "sessions")).toBe(200);
    expect(autoLimit([50, 100, 200, 500], 50, "sessions")).toBe(50);
    expect(autoLimit([50, 100, 200, 500], 9000, "sessions")).toBe(500);
  });

  it("starts from the past week when nothing was handed over", () => {
    const scope = lastWeekScope("sessions", new Date("2026-10-15T12:34:56Z"));
    expect(scope.label).toBe("Past 7 days");
    const [start, end] = scope.filters[0].filter_config.filter_value;
    expect(new Date(end) - new Date(start)).toBeGreaterThanOrEqual(
      7 * 24 * 3600 * 1000,
    );
    expect(new Date(start).getMinutes()).toBe(0);
  });
});
