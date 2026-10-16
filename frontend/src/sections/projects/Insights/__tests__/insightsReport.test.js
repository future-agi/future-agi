import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  assignAskRefs,
  buildPreviewIndex,
  buildRefIndex,
} from "../insightsCitations";
import { formatDayRange } from "../insightsFormat";
import { unitsGridLink } from "../insightsLinks";
import {
  askHistory,
  normalizeAnswer,
  readAskThread,
  reportMetaParts,
  suggestedQuestions,
  writeAskThread,
} from "../insightsReport";
import runFixture from "./fixtures/run_sessions_failures.json";
import enrichedFixture from "./fixtures/run_sessions_failures_enriched.json";
import askFixture from "./fixtures/ask_answer.json";

const run = runFixture.result;
const summary = enrichedFixture.result.result.summary;

// Node's own Web Storage globals can shadow jsdom's, so each test installs a
// plain in-memory one (or one that throws, like a blocked browser).
const memoryStorage = () => {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
    clear: () => data.clear(),
  };
};
const blockedStorage = () => ({
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
});

beforeEach(() => {
  vi.stubGlobal("sessionStorage", memoryStorage());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reportMetaParts", () => {
  it("says what was sampled, analysed and left out, who and when", () => {
    expect(reportMetaParts(run, "failures")).toEqual([
      "108 sessions sampled of 108",
      "72 analysed (34 had no failure)",
      "39 users",
      "Oct 9–15",
    ]);
  });

  it("explains a left-out item by the lens and skips users for a users report", () => {
    const users = {
      ...run,
      unit: "users",
      result: { ...run.result, totals: { count: 40 }, excluded: 6 },
    };
    expect(reportMetaParts(users, "personas")).toEqual([
      "108 users sampled of 108",
      "40 analysed (6 not relevant to the question)",
    ]);
  });
});

describe("formatDayRange", () => {
  const now = new Date("2026-10-15T12:00:00Z");
  it("collapses a month and adds the year only when it differs", () => {
    expect(
      formatDayRange("2026-10-08T12:00:00Z", "2026-10-15T12:00:00Z", now),
    ).toBe("Oct 8–15");
    expect(
      formatDayRange("2026-09-28T12:00:00Z", "2026-10-03T12:00:00Z", now),
    ).toBe("Sep 28 – Oct 3");
    expect(
      formatDayRange("2026-10-08T12:00:00Z", "2026-10-08T12:00:00Z", now),
    ).toBe("Oct 8");
    expect(
      formatDayRange("2025-12-30T12:00:00Z", "2026-01-02T12:00:00Z", now),
    ).toBe("Dec 30, 2025 – Jan 2, 2026");
  });
});

describe("citations of the new engine", () => {
  it("indexes refs that only appear in findings", () => {
    const index = buildRefIndex({
      findings: [{ refs: [{ ref: 21, unit_id: "u21" }] }],
    });
    expect(index.get(21)).toEqual({ unitId: "u21", categoryId: "" });
  });

  it("previews a cited unit with the highlight's title and the finding's quote", () => {
    const previews = buildPreviewIndex(summary);
    const unit11 = summary.highlights.find((h) => h.ref === 11).unit_id;
    expect(previews.get(unit11)).toMatchObject({
      title: "Asked for a human four times and was never offered one",
      quote: "no. a human. agent. representative",
      categoryId: "dbabb1b07db7",
    });
    // A finding's evidence alone still gives a quote.
    const unit4 = summary.highlights.find((h) => h.ref === 4).unit_id;
    expect(previews.get(unit4).quote).toBe("I just gave it to you. HP-72336.");
  });

  it("numbers Ask citations: report numbers first, then the next free ones", () => {
    const index = buildRefIndex(summary);
    const unit11 = index.get(11).unitId;
    const refs = assignAskRefs(
      [{ unit_ids: [unit11, "new-a"] }, { unit_ids: ["new-b", "new-a"] }],
      index,
    );
    expect(refs.get(unit11)).toBe(11);
    expect(refs.get("new-a")).toBe(17);
    expect(refs.get("new-b")).toBe(18);
    expect(assignAskRefs([{ unit_ids: ["z"] }], null).get("z")).toBe(1);
  });
});

describe("Ask helpers", () => {
  it("normalises an answer and keeps at most three follow-ups", () => {
    const { answer, followUps } = normalizeAnswer(askFixture.result);
    expect(answer).toHaveLength(2);
    expect(answer[0].category_ids).toEqual(["dbabb1b07db7"]);
    expect(answer[0].evidence[0].quote).toBe(
      "no. a human. agent. representative",
    );
    expect(followUps).toHaveLength(2);
    expect(normalizeAnswer({ answer: "Plain text." }).answer).toEqual([
      { text: "Plain text.", unit_ids: [], category_ids: [], evidence: [] },
    ]);
    expect(normalizeAnswer(null)).toEqual({
      answer: [],
      followUps: [],
      dropped: 0,
    });
    expect(normalizeAnswer({ answer: [], dropped: 2 }).dropped).toBe(2);
  });

  it("sends only finished turns as history", () => {
    expect(
      askHistory([
        {
          question: "Q1",
          status: "done",
          answer: [{ text: "A1" }, { text: "A2" }],
        },
        { question: "Q2", status: "error" },
      ]),
    ).toEqual([
      { role: "user", content: "Q1" },
      { role: "assistant", content: "A1\n\nA2" },
    ]);
  });

  it("sends the latest six turns, cut to the endpoint's limits", () => {
    const thread = Array.from({ length: 8 }, (_, i) => ({
      question: `Q${i}`,
      status: "done",
      answer: [{ text: "x".repeat(5000) }],
    }));
    const history = askHistory(thread);
    expect(history).toHaveLength(12);
    expect(history[0]).toEqual({ role: "user", content: "Q2" });
    expect(history[1].content).toHaveLength(4000);
  });

  it("keeps the thread per run in session storage, without turns in flight", () => {
    writeAskThread("run1", [
      { id: "1", question: "Q", status: "done", answer: [] },
      { id: "2", question: "Q2", status: "busy" },
    ]);
    expect(readAskThread("run1")).toEqual([
      { id: "1", question: "Q", status: "done", answer: [] },
    ]);
    expect(readAskThread("run2")).toEqual([]);
    vi.stubGlobal("sessionStorage", blockedStorage());
    expect(readAskThread("run1")).toEqual([]);
  });

  it("suggests questions that fit the report", () => {
    expect(
      suggestedQuestions({
        unit: "sessions",
        lens: "failures",
        hasPrevious: true,
      }),
    ).toEqual([
      "Which users are hit hardest?",
      "What changed since the last run?",
      "Show conversations where the agent recovered",
    ]);
    expect(suggestedQuestions({ unit: "users", lens: "personas" })[0]).toBe(
      "Which kinds of users fare worst?",
    );
  });
});

describe("unitsGridLink", () => {
  it("filters the Traces view to the units, covering their days", () => {
    const href = unitsGridLink("p1", "sessions", [
      { unit_id: "s1", session_id: "s1", time: "2026-10-10T10:00:00Z" },
      { unit_id: "s2", time: "2026-10-12T10:00:00Z" },
    ]);
    const url = new URL(href, "http://x");
    expect(url.pathname).toBe("/dashboard/observe/p1/llm-tracing");
    expect(JSON.parse(url.searchParams.get("observeLinkFilter"))).toEqual([
      {
        column_id: "session_id",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["s1", "s2"],
        },
      },
    ]);
    expect(url.searchParams.get("primaryTraceDateFilter")).toContain("Custom");
    expect(unitsGridLink("p1", "users", [{ unit_id: "u" }])).toBeNull();
    expect(unitsGridLink("p1", "traces", [])).toBeNull();
  });
});
