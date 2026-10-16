import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  answerCounts,
  answerState,
  defaultAnswer,
  resolveFocus,
} from "../askAnswers";
import { PIVOTS, buildPivot, shareLevel } from "../askPivot";
import {
  benjaminiHochberg,
  findPatterns,
  fisherExact,
  hoursText,
  minimumP,
  taroneCount,
} from "../askStats";
import {
  DAY,
  binText,
  buildTimeline,
  chooseBinMs,
  clampWindow,
  sampleStack,
  stackCap,
  rangeText,
  selectColumns,
} from "../timelineBins";
import {
  OUTAGE_COUNT,
  OUTAGE_ERRORS,
  QUESTIONS,
  REPEAT_USER,
  RESOLVED,
  TOPIC,
  syntheticRun,
} from "./fixtures/syntheticRun";

// The charts stop a window at "now"; pin it to the synthetic run's evening.
beforeAll(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 15, 18, 30));
});
afterAll(() => vi.useRealTimers());

const HOUR = 60 * 60 * 1000;
const run = syntheticRun();
const live = syntheticRun({ answered: 0.6 });

describe("answerState", () => {
  const record = (entry) => ({ id: "r", answers: entry ? { q: entry } : {} });
  it("tells waiting, reading, failed, skipped and answered apart", () => {
    expect(answerState(record(), "q").state).toBe("waiting");
    expect(answerState(record({ status: "running" }), "q").state).toBe(
      "reading",
    );
    expect(answerState(record({ answer: "__error__" }), "q").state).toBe(
      "failed",
    );
    expect(answerState(record({ status: "error" }), "q").state).toBe("failed");
    expect(answerState(record({ answer: "__skipped__" }), "q").state).toBe(
      "skipped",
    );
    expect(
      answerState(
        record({ answer: "No", confidence: 0.4, status: "completed" }),
        "q",
      ),
    ).toEqual({
      state: "answered",
      answer: "No",
      unsure: true,
      confidence: 0.4,
    });
    expect(
      answerState(record({ answer: "Yes", confidence: 0.9 }), "q").unsure,
    ).toBe(false);
  });

  it("defaults to No for yes/no and the rarest label otherwise", () => {
    expect(defaultAnswer(QUESTIONS[0], run.records)).toBe("No");
    const counts = answerCounts(run.records, QUESTIONS[1]).labels;
    const rarest = [...counts].sort((a, b) => a.count - b.count)[0].answer;
    expect(defaultAnswer(QUESTIONS[1], run.records)).toBe(rarest);
    expect(defaultAnswer(QUESTIONS[1], [])).toBe("Account change");
    expect(
      resolveFocus({ configId: TOPIC, answer: "Refund" }, QUESTIONS, []).answer,
    ).toBe("Refund");
    expect(
      resolveFocus({ configId: TOPIC, answer: "Nope" }, QUESTIONS, []).explicit,
    ).toBe(false);
  });

  it("counts answers, rows being read and rows not reached", () => {
    const counts = answerCounts(live.records, QUESTIONS[0]);
    const answered = counts.labels.reduce((sum, e) => sum + e.count, 0);
    expect(answered + counts.reading + counts.waiting).toBe(
      live.records.length,
    );
    expect(counts.reading).toBe(6);
  });
});

describe("timeline binning", () => {
  it("picks columns to fit about 90: a day in 15 minutes, a week in 2 hours, a month in 8", () => {
    expect(chooseBinMs(DAY)).toBe(15 * 60 * 1000);
    expect(chooseBinMs(7 * DAY)).toBe(2 * HOUR);
    expect(chooseBinMs(30 * DAY)).toBe(8 * HOUR);
    expect(chooseBinMs(0)).toBe(60 * 1000);
    expect(binText(2 * HOUR)).toBe("2-hour columns");
    expect(binText(15 * 60 * 1000)).toBe("15-minute columns");
  });

  it("keeps every bin of the window, starting at midnight, with one square per row", () => {
    const timeline = buildTimeline({
      records: run.records,
      configId: RESOLVED,
      question: QUESTIONS[0],
      window: run.window,
    });
    expect(timeline.binMs).toBe(2 * HOUR);
    // Six full days and today up to 6 PM, in 2-hour columns.
    expect(timeline.columns).toHaveLength(6 * 12 + 9);
    const placed = timeline.columns.reduce((sum, c) => sum + c.items.length, 0);
    expect(placed).toBe(run.records.length);
    expect(new Date(timeline.columns[0].start).getHours()).toBe(0);
    // Empty night columns stay as gaps.
    expect(
      timeline.columns.filter((c) => !c.items.length).length,
    ).toBeGreaterThan(20);
    expect(timeline.dayTicks).toHaveLength(7);
    expect(timeline.dayTicks.map((t) => t.at)).toEqual([
      0, 12, 24, 36, 48, 60, 72,
    ]);
  });

  it("stops a window that runs past now, and never cuts off a row", () => {
    const now = new Date(2026, 9, 15, 18, 30).getTime();
    const later = new Date(2026, 9, 16, 17, 0).toISOString();
    const start = new Date(2026, 9, 9).toISOString();
    expect(clampWindow([start, later], [], now)).toEqual([
      start,
      new Date(now).toISOString(),
    ]);
    const late = { time: new Date(2026, 9, 15, 20, 0).toISOString() };
    expect(clampWindow([start, later], [late], now)[1]).toBe(late.time);
    const earlier = new Date(2026, 9, 15, 12, 0).toISOString();
    expect(clampWindow([start, earlier], [], now)).toEqual([start, earlier]);
    expect(clampWindow(null, [], now)).toBe(null);
  });

  it("cuts columns at 1.5 × the 90th percentile, at least 12, keeping the mix", () => {
    const columns = (counts) =>
      counts.map((n) => ({ items: Array.from({ length: n }, (_, i) => i) }));
    // 20 non-empty columns: the 90th percentile is the 18th smallest.
    const counts = [0, 0, ...Array(17).fill(3), 10, 30, 40];
    expect(stackCap(columns(counts))).toBe(15);
    expect(stackCap(columns([1, 2, 3]))).toBe(12);
    expect(stackCap([])).toBe(12);
    // 20 rows, 14 "No" then 6 "Yes": 10 evenly spaced keep about 7 to 3.
    const stack = [...Array(14).fill("No"), ...Array(6).fill("Yes")];
    const sample = sampleStack(stack, 10);
    expect(sample).toHaveLength(10);
    expect(sample.filter((a) => a === "No")).toHaveLength(7);
    expect(sampleStack(stack, 30)).toBe(stack);
  });

  it("stacks each column by answer: labels first, then reading, then not yet", () => {
    const timeline = buildTimeline({
      records: live.records,
      configId: RESOLVED,
      question: QUESTIONS[0],
      window: live.window,
    });
    timeline.columns.forEach((column) => {
      const ranks = column.items.map((item) => item.rank);
      expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
    });
    const states = timeline.columns.flatMap((c) => c.items.map((i) => i.state));
    expect(new Set(states)).toEqual(
      new Set(["answered", "reading", "waiting"]),
    );
    const done = buildTimeline({
      records: run.records,
      configId: RESOLVED,
      question: QUESTIONS[0],
      window: run.window,
    });
    const outage = done.columns.find(
      (column) =>
        column.start === new Date(run.outage.day).setHours(14) &&
        column.items.length,
    );
    expect(outage.items.every((item) => item.answer === "No")).toBe(true);
  });

  it("selects a dragged range of columns and names it", () => {
    const timeline = buildTimeline({
      records: run.records,
      configId: RESOLVED,
      question: QUESTIONS[0],
      window: run.window,
    });
    const day = new Date(run.outage.day).getTime();
    const from = timeline.columns.findIndex((c) => c.start === day + 14 * HOUR);
    const { ids, label } = selectColumns(timeline.columns, from + 1, from);
    const expected = run.records
      .filter((r) => {
        const ms = new Date(r.time).getTime();
        return ms >= day + 14 * HOUR && ms < day + 18 * HOUR;
      })
      .map((r) => r.id);
    expect(new Set(ids)).toEqual(new Set(expected));
    run.outage.ids.forEach((id) => expect(ids).toContain(id));
    expect(label).toBe(`${rangeText(day + 14 * HOUR, day + 18 * HOUR)}`);
    expect(label).toMatch(/, 2 PM – 6 PM$/);
    expect(rangeText(day, day + 2 * DAY)).toMatch(/^\w+ \d+ – \w+ \d+$/);
  });
});

describe("Fisher exact and Benjamini–Hochberg", () => {
  it("matches known two-sided p-values", () => {
    // R: fisher.test(matrix(c(3, 1, 1, 3), 2))$p.value
    expect(fisherExact(3, 1, 1, 3)).toBeCloseTo(0.4857143, 6);
    // R: fisher.test(matrix(c(1, 11, 9, 3), 2))$p.value
    expect(fisherExact(1, 9, 11, 3)).toBeCloseTo(0.002759456, 8);
    // Perfect separation of 10 and 10: 2 / C(20, 10).
    expect(fisherExact(10, 0, 0, 10)).toBeCloseTo(2 / 184756, 12);
    expect(fisherExact(0, 0, 0, 0)).toBe(1);
  });

  it("corrects p-values for the number of comparisons", () => {
    const qs = benjaminiHochberg([0.01, 0.04, 0.03, 0.005]);
    [0.02, 0.04, 0.04, 0.02].forEach((q, i) =>
      expect(qs[i]).toBeCloseTo(q, 10),
    );
    expect(benjaminiHochberg([])).toEqual([]);
  });

  it("leaves out groups too small to ever stand out (Tarone)", () => {
    // 3 rows of 10, 5 of them hits: at best 3 of 3 or 0 of 3, p = 1/6.
    expect(minimumP(3, 10, 5)).toBeCloseTo(1 / 6, 10);
    // K = 1 leaves 2 groups that could reach 0.1; K = 2 leaves 2 under 0.05.
    expect(taroneCount([0.001, 0.02, 0.2, 0.5], 0.1)).toBe(2);
    expect(taroneCount([], 0.1)).toBe(0);
    // Two p-values corrected for 4 comparisons.
    const [a, b] = benjaminiHochberg([0.01, 0.04], 4);
    expect(a).toBeCloseTo(0.04, 10);
    expect(b).toBeCloseTo(0.08, 10);
  });

  it("writes hour ranges in words", () => {
    expect(hoursText(14, 17)).toBe("2–5 PM");
    expect(hoursText(11, 14)).toBe("11 AM–2 PM");
  });
});

describe("findPatterns", () => {
  const result = findPatterns({
    records: run.records,
    questions: QUESTIONS,
    focus: { configId: RESOLVED, answer: null },
  });

  it("finds the outage afternoon among the strongest, with its counts", () => {
    expect(result.answer).toBe("No");
    const first = result.patterns.find((p) => p.dimension === "window");
    expect(result.patterns.slice(0, 2)).toContain(first);
    expect(first.k).toBe(OUTAGE_COUNT);
    expect(first.n).toBe(OUTAGE_COUNT);
    expect(first.text).toMatch(
      new RegExp(
        `^“No” on \\w+ \\d+, 2–5 PM: 9 of 9 \\(100%\\) vs \\d+% elsewhere$`,
      ),
    );
    expect(new Set(first.ids)).toEqual(new Set(run.outage.ids));
    expect(first.q).toBeLessThan(0.1);
  });

  it("reports errors once and never the same afternoon twice", () => {
    const errors = result.patterns.filter((p) => p.dimension === "error");
    expect(errors.length).toBeLessThanOrEqual(1);
    if (errors.length) {
      expect(errors[0].text).toMatch(/when the run hit an error/);
      expect(errors[0].when).toMatch(
        new RegExp(`^all ${OUTAGE_ERRORS} on \\w+ \\d+, 2–5 PM$`),
      );
    }
    const windows = result.patterns.filter((p) => p.dimension === "window");
    expect(windows).toHaveLength(1);
    expect(result.patterns.length).toBeLessThanOrEqual(5);
    // Every callout's numbers are its own counts.
    result.patterns.forEach((p) => {
      expect(p.ids).toHaveLength(p.n);
      expect(p.text).toContain(`${p.k} of ${p.n}`);
    });
  });

  it("counts a group once when another holds the same rows", () => {
    // Every outage session, and only those, hit an error: the error group and
    // the afternoon are the same rows, so one callout names both.
    const outage = new Set(run.outage.ids);
    const same = findPatterns({
      records: run.records.map((record) => ({
        ...record,
        facets: { ...record.facets, error: outage.has(record.id) },
      })),
      questions: QUESTIONS,
      focus: { configId: RESOLVED, answer: null },
    });
    const error = same.patterns.find((p) => p.dimension === "error");
    expect(error.when).toMatch(
      new RegExp(`^all ${OUTAGE_COUNT} on \\w+ \\d+, 2–5 PM$`),
    );
    expect(error.text).toMatch(/elsewhere, all 9 on \w+ \d+, 2–5 PM$/);
    expect(same.patterns.some((p) => p.dimension === "window")).toBe(false);
    // Groups too small to ever stand out are not counted against the rest.
    expect(same.counted).toBeGreaterThan(0);
    expect(same.counted).toBeLessThan(same.tested);
  });

  it("finds the customer who never gets an answer", () => {
    const user = result.patterns.find((p) => p.where.includes(REPEAT_USER));
    expect(user).toBeTruthy();
    expect(user.k).toBe(user.n);
  });

  it("says nothing when nothing differs, or before there are answers", () => {
    const flat = run.records.map((record, i) => ({
      ...record,
      facets: {},
      user_id: "",
      end_user_id: "",
      answers: {
        [RESOLVED]: { answer: i % 2 ? "Yes" : "No", status: "completed" },
      },
    }));
    const quiet = findPatterns({
      records: flat,
      questions: [QUESTIONS[0]],
      focus: null,
    });
    expect(quiet.patterns).toEqual([]);
    expect(quiet.tested).toBeGreaterThan(0);
    const none = findPatterns({
      records: [],
      questions: QUESTIONS,
      focus: null,
    });
    expect(none.patterns).toEqual([]);
    expect(none.answered).toBe(0);
  });
});

describe("heatmap pivots", () => {
  it("lays out Day × Hour with the outage cells full of No", () => {
    const pivot = buildPivot({
      records: run.records,
      questions: QUESTIONS,
      configId: RESOLVED,
      answer: "No",
      window: run.window,
    });
    expect(pivot.rows).toHaveLength(7);
    expect(pivot.columns).toHaveLength(24);
    const day = run.outage.day;
    const key = `${day.getFullYear()}-${day.getMonth()}-${day.getDate()}`;
    const cells = [14, 15, 16].map((hour) => pivot.cells.get(`${key}|${hour}`));
    expect(cells.reduce((sum, c) => sum + c.matches, 0)).toBe(OUTAGE_COUNT);
    cells.forEach((cell) => expect(cell.matches).toBe(cell.answered));
    expect(pivot.overall.answered).toBe(run.records.length);
  });

  it("keeps the 15 busiest users, and models by day", () => {
    const users = buildPivot({
      records: run.records,
      questions: QUESTIONS,
      configId: RESOLVED,
      answer: "No",
      pivot: "userDay",
      window: run.window,
    });
    expect(users.rows).toHaveLength(15);
    expect(users.rows.map((r) => r.key)).toContain(REPEAT_USER);
    expect(users.hidden).toBeGreaterThan(0);
    const models = buildPivot({
      records: run.records,
      questions: QUESTIONS,
      configId: RESOLVED,
      answer: "No",
      pivot: "modelDay",
      window: run.window,
    });
    expect(models.rows.map((r) => r.key).sort()).toEqual([
      "claude-haiku-5-5",
      "gpt-4o-mini",
    ]);
  });

  it("crosses two questions' answers", () => {
    const pivot = buildPivot({
      records: run.records,
      questions: QUESTIONS,
      configId: RESOLVED,
      answer: "No",
      pivot: "questions",
    });
    expect(pivot.rows.map((r) => r.key)).toEqual(["Yes", "No"]);
    expect(pivot.columns.map((c) => c.key)).toEqual(QUESTIONS[1].labels);
    const total = [...pivot.cells.values()].reduce(
      (sum, c) => sum + c.total,
      0,
    );
    expect(total).toBe(run.records.length);
    expect(pivot.against.configId).toBe(TOPIC);
    expect(PIVOTS.map((p) => p.value)).toEqual([
      "dayHour",
      "userDay",
      "modelDay",
      "questions",
    ]);
  });

  it("maps a share to five steps", () => {
    expect([0, 0.1, 0.25, 0.26, 0.5, 0.74, 1].map(shareLevel)).toEqual([
      0, 1, 1, 2, 2, 3, 4,
    ]);
  });
});
