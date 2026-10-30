import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { filterParams } from "../../askFilter";
import {
  answerCounts,
  answerState,
  defaultAnswer,
  resolveFocus,
} from "../askAnswers";
import {
  PIVOTS,
  buildMatrix,
  buildTimeGrid,
  nicePeak,
  shareLevel,
  wilsonLower,
} from "../askPivot";
import { matchesFilter, summarizeRecords, tally } from "../askSummary";
import {
  benjaminiHochberg,
  buildGroups,
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

describe("long-range time pattern selections", () => {
  it("keeps every matching day when Show spans 401 dates", () => {
    const question = QUESTIONS[0];
    const records = Array.from({ length: 401 }, (_, i) => ({
      id: `morning-${i}`,
      time: new Date(2024, 0, 1 + i, 6).toISOString(),
      answers: {
        [question.configId]: {
          answer: "No",
          confidence: 0.9,
          status: "completed",
        },
      },
      facets: {},
    }));
    const afternoon = {
      ...records[0],
      id: "afternoon",
      time: new Date(2024, 0, 1, 15).toISOString(),
    };
    const summary = summarizeRecords({
      records: [...records, afternoon],
      questions: [question],
    });
    const { groups } = buildGroups(summary, [question], question, "sessions");
    const morning = groups.find((group) => group.key === "tod:6");
    expect(morning.filter.group).toHaveLength(401);
    expect(filterParams(morning.filter).group).toEqual(morning.filter.group);
    expect(
      records.every((record) => matchesFilter(record, morning.filter)),
    ).toBe(true);
    expect(matchesFilter(afternoon, morning.filter)).toBe(false);
  });
});

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
    const { count, filter, label } = selectColumns(
      timeline.columns,
      from + 1,
      from,
    );
    const expected = run.records
      .filter((r) => {
        const ms = new Date(r.time).getTime();
        return ms >= day + 14 * HOUR && ms < day + 18 * HOUR;
      })
      .map((r) => r.id);
    expect(filter).toEqual({
      time_from: new Date(day + 14 * HOUR).toISOString(),
      time_to: new Date(day + 18 * HOUR).toISOString(),
    });
    expect(count).toBe(expected.length);
    const ids = run.records
      .filter((r) => matchesFilter(r, filter))
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
    const selected = run.records.filter((r) => matchesFilter(r, first.filter));
    expect(new Set(selected.map((r) => r.id))).toEqual(new Set(run.outage.ids));
    expect(first.q).toBeLessThan(0.1);
  });

  it("reports errors once and never the same afternoon twice", () => {
    const errors = result.patterns.filter((p) => p.dimension === "error");
    expect(errors.length).toBeLessThanOrEqual(1);
    if (errors.length) {
      expect(errors[0].text).toMatch(/when the trace had an error/);
      expect(errors[0].when).toMatch(
        new RegExp(`^all ${OUTAGE_ERRORS} on \\w+ \\d+, 2–5 PM$`),
      );
    }
    const windows = result.patterns.filter((p) => p.dimension === "window");
    expect(windows).toHaveLength(1);
    expect(result.patterns.length).toBeLessThanOrEqual(5);
    // Every callout's numbers are its own counts, and its filter selects
    // exactly the rows it counted.
    result.patterns.forEach((p) => {
      const answered = run.records.filter(
        (r) =>
          matchesFilter(r, p.filter) &&
          answerState(r, RESOLVED).state === "answered",
      );
      expect(answered).toHaveLength(p.n);
      expect(
        answered.filter((r) => answerState(r, RESOLVED).answer === "No"),
      ).toHaveLength(p.k);
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
  const summary = summarizeRecords({
    records: run.records,
    questions: QUESTIONS,
    window: run.window,
  });
  const pad = (n) => String(n).padStart(2, "0");
  const dayKey = (date) =>
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

  it("lays out Day × Hour with the outage cells full of No", () => {
    const grid = buildTimeGrid({
      summary,
      question: QUESTIONS[0],
      window: run.window,
    });
    expect(grid.rows).toHaveLength(7);
    expect(grid.columns).toHaveLength(24);
    const key = dayKey(run.outage.day);
    const cells = [14, 15, 16].map((hour) => grid.cells.get(`${key}|${hour}`));
    const no = cells.reduce((sum, c) => sum + c.byLabel.get("No"), 0);
    expect(no).toBe(OUTAGE_COUNT);
    cells.forEach((cell) => expect(cell.byLabel.get("No")).toBe(cell.answered));
    expect(grid.overall.answered).toBe(run.records.length);
    // A cell selects its hour.
    const cell = grid.cells.get(`${key}|14`);
    expect(
      run.records.filter((r) => matchesFilter(r, cell.filter)),
    ).toHaveLength(cell.n);
  });

  it("sums the weeks by weekday, and goes to 3-hour blocks when hours are thin", () => {
    const week = buildTimeGrid({
      summary,
      question: QUESTIONS[0],
      pivot: "weekdayHour",
      window: run.window,
    });
    expect(week.rows.map((r) => r.label)).toEqual([
      "Mon",
      "Tue",
      "Wed",
      "Thu",
      "Fri",
      "Sat",
      "Sun",
    ]);
    expect(week.overall.answered).toBe(run.records.length);
    const dense = buildTimeGrid({
      summary,
      question: QUESTIONS[1],
      window: run.window,
      resolution: "dense",
    });
    expect(dense.step).toBeGreaterThan(1);
    expect(dense.columns).toHaveLength(24 / dense.step);
    expect(dense.overall.answered).toBe(run.records.length);
  });

  it("lists the busiest users, and models, by answer", () => {
    const users = buildMatrix({
      summary,
      questions: QUESTIONS,
      question: QUESTIONS[0],
      pivot: "user",
    });
    expect(users.rows).toHaveLength(15);
    expect(users.rows.map((r) => r.key)).toContain(REPEAT_USER);
    expect(users.hidden).toBeGreaterThan(0);
    // The customer who never gets an answer stands out as "No".
    expect(users.standOut.get(`${REPEAT_USER}|No`)).toBeTruthy();
    const models = buildMatrix({
      summary,
      questions: QUESTIONS,
      question: QUESTIONS[0],
      pivot: "model",
    });
    expect(models.rows.map((r) => r.key).sort()).toEqual([
      "claude-haiku-5-5",
      "gpt-4o-mini",
    ]);
    expect(models.cellFilter(models.rows[0], models.columns[1])).toEqual({
      group: `model:${models.rows[0].key}`,
      config_id: RESOLVED,
      answer: "No",
    });
  });

  it("crosses two questions' answers", () => {
    const matrix = buildMatrix({
      summary,
      questions: QUESTIONS,
      question: QUESTIONS[0],
      pivot: "questions",
    });
    expect(matrix.rows.map((r) => r.key)).toEqual(["Yes", "No"]);
    expect(matrix.columns.map((c) => c.key)).toEqual(QUESTIONS[1].labels);
    const total = matrix.rows.reduce((sum, row) => sum + row.answered, 0);
    expect(total).toBe(run.records.length);
    expect(matrix.against.configId).toBe(TOPIC);
    const filter = matrix.cellFilter(matrix.rows[1], matrix.columns[0]);
    const k = run.records.filter((r) => matchesFilter(r, filter)).length;
    expect(k).toBe(matrix.rows[1].counts.get(QUESTIONS[1].labels[0]));
    expect(PIVOTS.map((p) => p.value)).toEqual([
      "dayHour",
      "weekdayHour",
      "modelDay",
      "user",
      "model",
      "turns",
    ]);
  });

  it("maps a share to five steps, up to a peak", () => {
    expect(
      [0, 0.1, 0.25, 0.26, 0.5, 0.74, 1].map((s) => shareLevel(s)),
    ).toEqual([0, 1, 1, 2, 2, 3, 4]);
    expect(shareLevel(0.4, 0.4)).toBe(4);
    expect(shareLevel(0.2, 0.4)).toBe(2);
    expect(nicePeak([0.12, 0.31])).toBe(0.4);
    expect(nicePeak([0.93])).toBe(1);
    expect(nicePeak([])).toBe(0.1);
  });

  it("shades a cell by the share it surely has", () => {
    // 4 of 4 is not "surely 100%": the 95% Wilson interval starts near 51%.
    expect(wilsonLower(4, 4)).toBeCloseTo(0.5101, 3);
    expect(wilsonLower(400, 1000)).toBeCloseTo(0.3701, 3);
    expect(wilsonLower(0, 10)).toBe(0);
    expect(wilsonLower(0, 0)).toBe(0);
  });
});

describe("tally of one question in a multi-question bin", () => {
  it("never counts another question's failures as this one's", () => {
    // `failed` is per row: with two questions it counts B's failures too.
    const entry = {
      n: 10,
      failed: 4,
      counts: { a: { Yes: 6, No: 4 }, b: { Yes: 2, __error__: 4 } },
    };
    expect(tally(entry, "a", ["Yes", "No"]).failed).toBe(0);
    expect(tally(entry, "b", ["Yes", "No"]).failed).toBe(4);
    // One question: the row count is exact.
    const one = { n: 10, failed: 3, counts: { a: { Yes: 7 } } };
    expect(tally(one, "a", ["Yes", "No"]).failed).toBe(3);
  });
});
