import { describe, expect, it } from "vitest";
import { paramsSerializer } from "src/utils/utils";
import { askLink, readHandoff } from "src/sections/projects/observeHandoff";
import { OBSERVE_LINK_FILTER_PARAM } from "src/sections/projects/LLMTracing/common";
import {
  displayQuestions,
  runStatus,
  summaryQuestions,
  summaryWindow,
  toRecord,
} from "../useAskRun";
import {
  GRID_LINK_MAX,
  rowsGridLink,
  sourceViewLink,
  tabForUnit,
} from "../askLinks";
import {
  dayGroup,
  facetLine,
  formatElapsed,
  runTime,
  scopeWords,
} from "../askFormat";
import {
  choiceCost,
  defaultChoice,
  handedIds,
  maxSampleSize,
  parseSampleSize,
  sampleSizeProblem,
  sampleSizes,
  withSampleChoice,
} from "../askPopulation";
import {
  creditCount,
  creditWords,
  describeFilter,
  durationWords,
  estimateWords,
  filterParams,
  mergeFilters,
  sampleEstimate,
  wilson,
  answeredSplit,
  boundWords,
  unansweredBounds,
  UNANSWERED_BOUND_SHARE,
} from "../askFilter";
import { rerunScope, seedScope } from "../askScope";
import { presetWindow, windowItem } from "src/sections/projects/observeScope";

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
});

describe("a run read from its summary", () => {
  it("says where the run is", () => {
    expect(runStatus(null)).toBe("loading");
    expect(runStatus({ status: "pending", preparing: true })).toBe("preparing");
    expect(runStatus({ status: "running", preparing: false })).toBe("running");
    expect(runStatus({ status: "paused" })).toBe("paused");
    expect(runStatus({ status: "cancelled" })).toBe("cancelled");
    expect(runStatus({ status: "completed" })).toBe("done");
    // Failed before a row was read; one that stopped partway is stalled.
    expect(runStatus({ status: "failed", total: 0, pending: 0 })).toBe(
      "failed",
    );
    expect(runStatus({ status: "stalled" })).toBe("stalled");
  });

  it("takes its questions from the summary and the saved evaluators", () => {
    const summary = {
      questions: [
        {
          config_id: C1,
          question: "Was it resolved?",
          answer_type: "yes_no",
          labels: ["Yes", "No"],
        },
      ],
    };
    expect(
      summaryQuestions(summary, {
        [C1]: { reads: "transcript", templateId: "t1", definitions: {} },
      }),
    ).toEqual([
      {
        configId: C1,
        question: "Was it resolved?",
        answerType: "yes_no",
        labels: ["Yes", "No"],
        definitions: {},
        reads: "transcript",
        templateId: "t1",
        fullQuestion: "Was it resolved?",
      },
    ]);
  });

  it("shows questions without the prefix every one of them shares", () => {
    expect(
      displayQuestions([
        "Synthetic scale test · What did the customer come for?",
        "Synthetic scale test · Did the agent resolve the customer's issue?",
      ]),
    ).toEqual([
      "What did the customer come for?",
      "Did the agent resolve the customer's issue?",
    ]);
    expect(displayQuestions(["Instant eval · Was it resolved?"])).toEqual([
      "Was it resolved?",
    ]);
    expect(displayQuestions(["Tone · polite?", "Was it resolved?"])).toEqual([
      "Tone · polite?",
      "Was it resolved?",
    ]);
    expect(formatElapsed(21000)).toBe("21 s");
    expect(formatElapsed(35 * 60000 + 3000)).toBe("35 min");
    expect(formatElapsed(125 * 60000)).toBe("2 h 5 min");
  });

  it("draws the timeline's window", () => {
    expect(summaryWindow({ timeline: { from: 0, to: 3600000 } })).toEqual([
      "1970-01-01T00:00:00.000Z",
      "1970-01-01T01:00:00.000Z",
    ]);
    expect(summaryWindow({ timeline: {} })).toBeNull();
  });
});

describe("what the charts select, as a filter", () => {
  it("turns a selection into the rows endpoint's parameters", () => {
    expect(
      filterParams({
        time_from: 1792000000000,
        time_to: "2026-10-13T17:00:00Z",
        config_id: C1,
        answer: "No",
        group: ["weekday:6", "weekday:7"],
      }),
    ).toEqual({
      time_from: new Date(1792000000000).toISOString(),
      time_to: "2026-10-13T17:00:00.000Z",
      config_id: C1,
      answer: "No",
      group: ["weekday:6", "weekday:7"],
    });
    expect(
      filterParams({
        answers: [{ config_id: C1, answer: "No" }],
        unsure: true,
      }),
    ).toEqual({
      answers: JSON.stringify([{ config_id: C1, answer: "No" }]),
      unsure: true,
    });
    expect(filterParams(null)).toEqual({});
  });

  it("sends each group as its own parameter, a comma kept inside its key", () => {
    // A 30-day "6-9 AM" pattern selects a key per day: one joined value
    // outgrew the server's 300-character item, and split keys with commas.
    const days = Array.from(
      { length: 30 },
      (_, i) => `hour3:2026-09-${String(i + 1).padStart(2, "0")}T06`,
    );
    const query = paramsSerializer().serialize(
      filterParams({ group: ["user:Doe, Jane", ...days] }),
    );
    const groups = new URLSearchParams(query).getAll("group");
    expect(groups).toEqual(["user:Doe, Jane", ...days]);
    expect(Math.max(...groups.map((g) => g.length))).toBeLessThan(300);
  });

  it("combines a focused answer with a selection", () => {
    expect(
      mergeFilters(
        { config_id: C1, answer: "No" },
        { config_id: C2, answer: "Order status", time_from: "x" },
      ),
    ).toEqual({
      time_from: "x",
      answers: [
        { config_id: C1, answer: "No" },
        { config_id: C2, answer: "Order status" },
      ],
    });
  });

  it("names a filter the charts didn't label", () => {
    const questions = [{ configId: C1 }, { configId: C2 }];
    expect(describeFilter({ config_id: C2, answer: "No" }, questions)).toBe(
      "“No” (Q2)",
    );
    expect(describeFilter({ group: "model:gpt-4.1-mini" }, [])).toBe(
      "model gpt-4.1-mini",
    );
  });
});

describe("a sample's estimate for the whole view", () => {
  it("is a Wilson interval narrowed by the share of the view read", () => {
    const open = wilson(380, 1000, 104233);
    expect(open.p).toBeCloseTo(0.38);
    expect(open.lo).toBeCloseTo(0.35, 2);
    expect(open.hi).toBeCloseTo(0.41, 2);
    const most = wilson(380, 1000, 1100);
    expect(most.hi - most.lo).toBeLessThan((open.hi - open.lo) / 2);
    expect(wilson(380, 1000, 1000)).toEqual({ p: 0.38, lo: 0.38, hi: 0.38 });
    expect(estimateWords("No", open)).toBe("≈ 38% No (95% CI 35–41%)");
  });

  it("always holds the share it estimates, however lopsided the sample", () => {
    // 10 of 10 No, read from a view of 49: "≈ 100% No (95% CI 73–99%)".
    const all = wilson(10, 10, 49);
    expect(all.hi).toBe(1);
    expect(estimateWords("No", all)).toBe("≈ 100% No (95% CI 76–100%)");
    expect(wilson(0, 10, 49).lo).toBe(0);
    for (const [x, n, N] of [
      [1, 10, 49],
      [9, 10, 12],
      [380, 1000, 1100],
      [3, 40, 41],
    ]) {
      const { p, lo, hi } = wilson(x, n, N);
      expect(lo).toBeLessThanOrEqual(p);
      expect(hi).toBeGreaterThanOrEqual(p);
    }
  });

  it("estimates the problem answer, or the one in focus", () => {
    const run = {
      sample: { is_sample: true, population: 104233 },
      summary: { counts: { [C1]: { Yes: 620, No: 380, __error__: 12 } } },
    };
    const question = { configId: C1, answerType: "yes_no" };
    expect(sampleEstimate(run, question, null).words).toBe(
      "≈ 38% No (95% CI 35–41%)",
    );
    expect(sampleEstimate(run, question, "Yes").label).toBe("Yes");
    expect(
      sampleEstimate({ ...run, sample: { is_sample: false } }, question),
    ).toBeNull();
  });

  it("gives a category question's three most common answers", () => {
    const run = {
      sample: { is_sample: true, population: 100000 },
      summary: {
        counts: {
          [C1]: {
            "Order status": 7624,
            "Return or refund": 6362,
            "Product advice": 3862,
            "Something else": 3803,
            __error__: 135,
          },
        },
      },
    };
    const question = { configId: C1, answerType: "category" };
    const estimate = sampleEstimate(run, question, null);
    expect(estimate.label).toBe("Order status");
    expect(estimate.parts).toHaveLength(3);
    expect(estimate.parts[0]).toMatch(/^≈ 35% Order status \(35–36%\)$/);
    expect(estimate.parts[1]).toMatch(/^29% Return or refund \(\d+–\d+%\)$/);
    expect(estimate.parts[2]).toMatch(/^18% Product advice/);
    // One answer in focus: just that one, with its interval.
    expect(sampleEstimate(run, question, "Something else").parts).toEqual([
      expect.stringMatching(/^≈ 18% Something else \(95% CI/),
    ]);
  });
});

describe("how much of a view to read", () => {
  const estimate = {
    population: 104233,
    confirm_rows: 5000,
    max_rows: 100000,
    default_sample_size: 1000,
    credits_per_answer: 0.06,
    choices: [
      { sample_size: 500, rows_to_read: 500, seconds_estimate: 110 },
      { sample_size: 1000, rows_to_read: 1000, seconds_estimate: 210 },
      { sample_size: null, rows_to_read: 104233, seconds_estimate: 20857 },
    ],
  };

  it("offers a sample by default above 1,000 rows", () => {
    expect(sampleSizes(estimate)).toEqual([500, 1000]);
    expect(defaultChoice(estimate)).toBe(1000);
    expect(defaultChoice({ ...estimate, default_sample_size: null })).toBe(
      null,
    );
  });

  it("costs each choice for the questions asked", () => {
    expect(choiceCost(estimate, 1000, 2)).toEqual({
      size: 1000,
      rows: 1000,
      credits: 120,
      seconds: 410,
      needsConfirm: false,
      creditLimited: false,
      creditsLeft: null,
      blocked: false,
      fitsSize: null,
      allowed: true,
    });
    // 90 credits left pay for 750 rows of two questions.
    const capped = {
      ...estimate,
      credit_check: { allowed: false, credits_left: 90, blocked: false },
    };
    expect(choiceCost(capped, 1000, 2)).toMatchObject({
      creditLimited: true,
      fitsSize: 750,
      allowed: false,
    });
    expect(choiceCost(capped, 500, 2)).toMatchObject({
      creditLimited: false,
      allowed: true,
    });
    // A size the server didn't cost is scaled from reading every row.
    expect(choiceCost(capped, 750, 2)).toMatchObject({
      rows: 750,
      credits: 90,
      creditLimited: false,
      allowed: true,
    });
    // A paused budget or an unpaid invoice stops every size.
    const blocked = {
      ...estimate,
      credit_check: { allowed: false, credits_left: null, blocked: true },
    };
    expect(choiceCost(blocked, 500, 1)).toMatchObject({
      creditLimited: true,
      fitsSize: null,
      allowed: false,
    });
    const all = choiceCost(estimate, null, 1);
    expect(all).toMatchObject({ rows: 104233, needsConfirm: true });
    expect(all.allowed).toBe(false);
    expect(durationWords(20857)).toBe("about 5 h 48 min");
  });

  it("costs credits at the server's rate, and none where nothing is billed", () => {
    expect(
      choiceCost({ ...estimate, credits_per_answer: 0.05 }, 1000, 2).credits,
    ).toBeCloseTo(100);
    // Self-hosted, or no billing set up: rows and time only.
    const unbilled = {
      ...estimate,
      credits_per_answer: null,
      credit_check: { allowed: true, credits_left: null, blocked: false },
    };
    expect(choiceCost(unbilled, 1000, 2)).toEqual({
      size: 1000,
      rows: 1000,
      credits: null,
      seconds: 410,
      needsConfirm: false,
      creditLimited: false,
      creditsLeft: null,
      blocked: false,
      fitsSize: null,
      allowed: true,
    });
    // Reading every row still needs a confirm past the confirm size.
    expect(choiceCost(unbilled, null, 1)).toMatchObject({
      credits: null,
      needsConfirm: true,
    });
    // Never a client-side price: no rate, no credits.
    expect(
      choiceCost({ ...estimate, credits_per_answer: undefined }, 1000, 1)
        .credits,
    ).toBeNull();
  });

  it("prints credits whole from 10 up and to a tenth below", () => {
    expect(creditCount(0.72)).toBe("0.7 credits");
    expect(creditCount(1.04)).toBe("1 credit");
    expect(creditCount(0)).toBe("0 credits");
    expect(creditCount(9.96)).toBe("10 credits");
    expect(creditCount(6000)).toBe("6,000 credits");
    expect(creditWords(0.06)).toBe("about 0.1 credits");
    expect(creditWords(60)).toBe("about 60 credits");
  });

  it("reads the rows handed over by id", () => {
    expect(handedIds({ population: [{ id: "a" }, { id: 2 }, {}] })).toEqual([
      "a",
      "2",
    ]);
    expect(handedIds({ ids: ["x", null] })).toEqual(["x"]);
    expect(handedIds({ filters: [] })).toBeNull();
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
    // More rows than one link names: refused, never cut short.
    const many = Array.from({ length: GRID_LINK_MAX + 1 }, (_, n) =>
      record(`s${n}`, "2026-10-13T14:00:00Z"),
    );
    expect(rowsGridLink("p1", "sessions", many)).toBeNull();
    expect(
      rowsGridLink("p1", "sessions", many.slice(0, GRID_LINK_MAX)),
    ).toContain("llm-tracing");
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
  const NOW = new Date("2026-10-15T12:34:56Z");

  it("starts from the past week when nothing was handed over", () => {
    const scope = seedScope(null, "sessions", NOW);
    expect(scope).toMatchObject({
      unit: "sessions",
      dateOption: "7D",
      filters: [],
      label: "Past 7D",
    });
    expect(scope.window).toEqual(presetWindow("7D", NOW));
    expect(new Date(scope.window[0]).getUTCMinutes()).toBe(0);
  });

  it("asks about a selection by id, labelled", () => {
    const scope = seedScope(
      { unit: "sessions", population: [{ id: "s1" }, { id: "s2" }] },
      "sessions",
      NOW,
    );
    expect(scope.ids).toEqual(["s1", "s2"]);
    expect(scope.label).toBe("2 selected sessions");
  });
});

describe("a run's scope asked again", () => {
  const NOW = new Date("2026-10-22T08:00:00Z");
  const STATUS = {
    column_id: "status",
    filter_config: { filter_op: "equals" },
  };
  const OLD = ["2026-10-01T00:00:00.000Z", "2026-10-08T00:00:00.000Z"];

  it("moves a preset's window to now", () => {
    expect(
      rerunScope(
        { filters: [windowItem(OLD), STATUS], date_preset: "30D" },
        {},
        NOW,
      ),
    ).toEqual({
      filters: [STATUS, windowItem(presetWindow("30D", NOW))],
      window: presetWindow("30D", NOW),
      datePreset: "30D",
      label: "Past 30D · 1 filter",
    });
  });

  it("keeps a custom range, an older run's window and rows by id", () => {
    const filters = [STATUS, windowItem(OLD)];
    expect(
      rerunScope(
        { filters, date_preset: "Custom", label: "Custom range · 1 filter" },
        {},
        NOW,
      ),
    ).toEqual({
      filters,
      window: OLD,
      datePreset: "Custom",
      label: "Custom range · 1 filter",
    });
    expect(rerunScope({ filters }, { label: "Past 7D" }, NOW)).toMatchObject({
      filters,
      datePreset: undefined,
      label: "Past 7D",
    });
    expect(
      rerunScope({ filters, ids: ["u1"], label: "1 selected user" }, {}, NOW),
    ).toMatchObject({ filters, label: "1 selected user" });
  });
});

describe("rows Jev couldn't answer", () => {
  // The finished Sample 1,000 run: 568 Yes, 325 No, 107 couldn't be answered.
  const run = {
    sample: { is_sample: true, population: 24473, read: 1000 },
    summary: { counts: { [C1]: { Yes: 568, No: 325, __error__: 107 } } },
  };
  const yesNo = { configId: C1, answerType: "yes_no" };

  it("bounds a share by counting them all one way, then the other", () => {
    const bounds = unansweredBounds(run, yesNo, ["No"]);
    expect(bounds.failed).toBe(107);
    expect(bounds.n).toBe(1000);
    expect(bounds.share).toBeGreaterThan(UNANSWERED_BOUND_SHARE);
    const [no] = bounds.bounds;
    // 325/1,000 at the low end and 432/1,000 at the high end, each with
    // its 95% interval.
    expect(no.lo).toBeCloseTo(wilson(325, 1000, 24473).lo, 6);
    expect(no.hi).toBeCloseTo(wilson(432, 1000, 24473).hi, 6);
    expect(no.lo).toBeLessThan(0.325);
    expect(no.hi).toBeGreaterThan(0.432);
    expect(boundWords(bounds, yesNo)).toMatch(
      /^107 of 1,000 couldn't be answered\. If they were all Yes or all No, No would be \d+–\d+%\.$/,
    );
  });

  it("gives each of a category question's answers its own bounds", () => {
    const category = { configId: C1, answerType: "category" };
    const counts = {
      "Order status": 300,
      Refund: 250,
      Other: 350,
      __error__: 100,
    };
    const bounds = unansweredBounds(
      { ...run, summary: { counts: { [C1]: counts } } },
      category,
      ["Other", "Order status"],
    );
    expect(boundWords(bounds, category)).toMatch(
      /^100 of 1,000 couldn't be answered\. Counting them as all or none of each answer: Other \d+–\d+% · Order status \d+–\d+%\.$/,
    );
  });

  it("is only a note when few rows went unanswered, and nothing when none did", () => {
    const few = unansweredBounds(
      {
        ...run,
        summary: { counts: { [C1]: { Yes: 600, No: 390, __error__: 10 } } },
      },
      yesNo,
      ["No"],
    );
    expect(few.share).toBeLessThanOrEqual(UNANSWERED_BOUND_SHARE);
    expect(
      unansweredBounds(
        { ...run, summary: { counts: { [C1]: { Yes: 600, No: 400 } } } },
        yesNo,
        ["No"],
      ),
    ).toBeNull();
  });

  it("splits a past run over the rows Jev answered, as its page does", () => {
    const split = answeredSplit(
      { labels: ["Yes", "No"] },
      { Yes: 568, No: 325, __error__: 107 },
    );
    expect(split).toMatchObject({ answered: 893, failed: 107 });
    expect(Math.round(split.labels[0].share * 100)).toBe(64);
  });
});

describe("how long a run took", () => {
  it("counts the time it ran, with the time it sat idle apart", () => {
    const run = {
      elapsedMs: 15240000,
      summary: { active_s: 2460, idle_s: 12780 },
    };
    expect(runTime(run, " in ")).toBe(" in 41 min (idle 3 h 33 min)");
    expect(runTime(run, "running ")).toBe("running 41 min (idle 3 h 33 min)");
    // A minute between answers is still running.
    expect(
      runTime({ summary: { active_s: 2460, idle_s: 60 } }, "running "),
    ).toBe("running 41 min");
    // A summary without the split: the wall time since it started.
    expect(runTime({ elapsedMs: 21000, summary: {} }, " in ")).toBe(" in 21 s");
  });
});

describe("a sample size typed in", () => {
  const view = { population: 24473, max_rows: 100000, sessions_per_user: null };

  it("reads only whole numbers", () => {
    expect(parseSampleSize("1500")).toBe(1500);
    expect(parseSampleSize(" 42 ")).toBe(42);
    expect(parseSampleSize("1.5")).toBeNaN();
    expect(parseSampleSize("")).toBeNaN();
    expect(parseSampleSize("-3")).toBeNaN();
  });

  const SESSIONS = { noun: "session", plural: "sessions" };
  const USERS = { noun: "user", plural: "users" };

  it("is 1 up to the view's rows and the run's limit, as the server says", () => {
    expect(sampleSizeProblem(1500, view, SESSIONS)).toBeNull();
    for (const size of [0, NaN])
      expect(sampleSizeProblem(size, view, SESSIONS)).toEqual({
        message: "Choose a sample of at least 1.",
        max: null,
      });
    expect(sampleSizeProblem(30000, view, SESSIONS)).toEqual({
      message:
        "There are only 24,473 sessions in this view; choose up to 24,473.",
      max: 24473,
    });
    const big = { ...view, population: 250000 };
    expect(sampleSizeProblem(120000, big, SESSIONS)).toEqual({
      message: "One run reads at most 100,000 rows; choose up to 100,000.",
      max: 100000,
    });
    expect(maxSampleSize(view)).toBe(24473);
    expect(maxSampleSize(big)).toBe(100000);
  });

  it("counts users, each read as their latest sessions", () => {
    const users = { population: 30000, max_rows: 100000, sessions_per_user: 5 };
    expect(maxSampleSize(users)).toBe(20000);
    expect(sampleSizeProblem(20000, users, USERS)).toBeNull();
    expect(sampleSizeProblem(20001, users, USERS)).toEqual({
      message:
        "One run reads at most 100,000 rows (20,000 users at 5 sessions each); choose up to 20,000.",
      max: 20000,
    });
    // A count still climbing is held to the rows alone.
    const climbing = {
      ...users,
      population: 300,
      population_is_lower_bound: true,
    };
    expect(sampleSizeProblem(900, climbing, USERS)).toBeNull();
    expect(sampleSizeProblem(25000, climbing, USERS)?.max).toBe(20000);
  });

  it("is costed as the server costs it", () => {
    const estimate = {
      population: 24473,
      max_rows: 100000,
      confirm_rows: 5000,
      choices: [
        { sample_size: 1000, rows_to_read: 1000, seconds_estimate: 210 },
        { sample_size: null, rows_to_read: 24473, seconds_estimate: 4900 },
      ],
    };
    const costed = withSampleChoice(estimate, {
      size: 1500,
      estimate: {
        rows_to_read: 1500,
        seconds_estimate: 320,
        needs_confirm: false,
      },
    });
    expect(choiceCost(costed, 1500)).toMatchObject({
      rows: 1500,
      seconds: 320,
      needsConfirm: false,
      allowed: true,
    });
    // The fixed sizes the menu offers stay as they were.
    expect(sampleSizes(estimate)).toEqual([1000]);
    expect(withSampleChoice(estimate, { size: null })).toBe(estimate);
  });
});
