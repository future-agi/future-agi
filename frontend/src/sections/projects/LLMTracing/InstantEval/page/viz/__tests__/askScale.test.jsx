import React from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { AskCrossTab, AskHeatmap, AskPatterns, AskTimeline } from "../index";
import { fisherExact, findPatterns } from "../askStats";
import {
  groupKeysOf,
  matchesFilter,
  summarizeRecords,
  tally,
} from "../askSummary";
import { answerState } from "../askAnswers";
import {
  CATCH_ALL_COLOR,
  CATEGORY_COLORS,
  CATEGORY_COLORS_DARK,
  NO_COLOR,
  OTHER_CATEGORY_COLOR,
  YES_COLOR,
  answerColor,
  isCatchAll,
} from "../../../results/answerColors";
import { buildMatrix } from "../askPivot";
import real from "./fixtures/askRunCategories.json";
import {
  NOW,
  PURPOSE,
  PURPOSE_LABELS,
  QUESTIONS,
  RESOLVED,
  bigSummary,
  smallRun,
} from "./fixtures/scaleRun";

vi.mock("src/components/iconify", () => ({ default: () => null }));

beforeAll(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW.getTime() + 30 * 60 * 1000));
});
afterAll(() => vi.useRealTimers());

const theme = createTheme({ palette: palette("light"), typography });
const small = smallRun();
// A month of 100,000 sessions, as counts only.
const big = bigSummary();

function renderViz(Component, props = {}) {
  const handlers = { onFocus: vi.fn(), onSelect: vi.fn(), onOpen: vi.fn() };
  const view = render(
    <ThemeProvider theme={theme}>
      <Component
        questions={QUESTIONS}
        focus={{ configId: RESOLVED, answer: null }}
        running={false}
        unit="sessions"
        {...handlers}
        {...props}
      />
    </ThemeProvider>,
  );
  return { ...view, ...handlers };
}

describe("the summary built from rows", () => {
  const summary = summarizeRecords({
    records: small.records,
    questions: QUESTIONS,
    window: small.window,
    unit: "sessions",
  });

  it("has the server's shape and key formats", () => {
    expect(summary.total).toBe(small.records.length);
    expect(Object.keys(summary.groups).sort()).toEqual(
      [
        "day",
        "day_hour",
        "error",
        "hour3",
        "model",
        "model_day",
        "turns",
        "user",
        "user_by_answer",
        "weekday",
      ].sort(),
    );
    summary.groups.day_hour.forEach((e) =>
      expect(e.key).toMatch(/^\d{4}-\d{2}-\d{2}\|\d{2}$/),
    );
    summary.groups.hour3.forEach((e) =>
      expect(e.key).toMatch(/^\d{4}-\d{2}-\d{2}\|(00|03|06|09|12|15|18|21)$/),
    );
    summary.groups.weekday.forEach((e) => expect(e.key).toMatch(/^[1-7]$/));
    expect(summary.groups.error.map((e) => e.key).sort()).toEqual([
      "error",
      "ok",
    ]);
    summary.timeline.bins.forEach((bin) => expect(typeof bin.t).toBe("number"));
    expect(summary.timeline.bins.length).toBeLessThanOrEqual(120);
    expect(summary.cross[RESOLVED][PURPOSE]).toBeTruthy();
    expect(summary.cross[PURPOSE][RESOLVED]).toBeTruthy();
  });

  it("counts every group exactly as its filter selects rows", () => {
    const { user_by_answer: byAnswer, ...dimensions } = summary.groups;
    // The users picked for each answer are users: `user:<key>` selects them.
    const picked = Object.values(byAnswer).flatMap((lists) =>
      Object.values(lists).flat(),
    );
    expect(picked.length).toBeGreaterThan(0);
    Object.entries({
      ...dimensions,
      user: [...dimensions.user, ...picked],
    }).forEach(([dimension, entries]) =>
      entries.forEach((entry) => {
        const rows = small.records.filter((r) =>
          matchesFilter(r, { group: `${dimension}:${entry.key}` }),
        );
        expect(rows).toHaveLength(entry.n);
        const t = tally(entry, RESOLVED, ["Yes", "No"]);
        expect(
          rows.filter((r) => answerState(r, RESOLVED).answer === "No"),
        ).toHaveLength(t.byLabel.get("No"));
      }),
    );
    // Bins add up to the run.
    const binned = summary.timeline.bins.reduce((sum, b) => sum + b.n, 0);
    expect(binned).toBe(small.records.length);
    expect(groupKeysOf(small.records[0]).user).toBe(
      small.records[0].end_user_id,
    );
  });
});

describe("Fisher at scale", () => {
  it("walks out from the mode to the same p as summing every table", () => {
    // Support of 201 values: the walk, against a brute-force sum.
    const brute = (a, b, c, d) => {
      const lf = (n) => {
        let s = 0;
        for (let i = 2; i <= n; i += 1) s += Math.log(i);
        return s;
      };
      const r1 = a + b;
      const c1 = a + c;
      const n = a + b + c + d;
      const lp = (x) =>
        lf(c1) -
        lf(x) -
        lf(c1 - x) +
        lf(n - c1) -
        lf(r1 - x) -
        lf(n - c1 - r1 + x) -
        (lf(n) - lf(r1) - lf(n - r1));
      const obs = Math.exp(lp(a));
      let p = 0;
      for (let x = Math.max(0, r1 + c1 - n); x <= Math.min(r1, c1); x += 1) {
        const px = Math.exp(lp(x));
        if (px <= obs * (1 + 1e-7)) p += px;
      }
      return p;
    };
    [
      [120, 80, 300, 500],
      [90, 110, 400, 400],
      [40, 160, 500, 300],
    ].forEach(([a, b, c, d]) =>
      expect(fisherExact(a, b, c, d)).toBeCloseTo(brute(a, b, c, d), 10),
    );
    // A 100,000-row table is quick and tiny.
    const t0 = Date.now();
    const p = fisherExact(9000, 2000, 36000, 53000);
    expect(p).toBeLessThan(1e-100);
    expect(Date.now() - t0).toBeLessThan(200);
  });
});

describe("patterns from counts, at 100,000 rows", () => {
  it("finds the planted patterns for a category question, across all its answers", () => {
    const result = findPatterns({
      summary: big.summary,
      questions: QUESTIONS,
      focus: { configId: PURPOSE, answer: null },
    });
    expect(result.answer).toBe(null);
    expect(result.answered).toBe(100000);
    const texts = result.patterns.map((p) => p.text);
    expect(texts).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/^“Return or refund” at weekends: /),
        expect.stringMatching(/^“Order status” on \w+ \d+, 2–5 PM: /),
      ]),
    );
    expect(new Set(result.patterns.map((p) => p.label)).size).toBeGreaterThan(
      1,
    );
    const weekend = result.patterns.find((p) => p.dimension === "weekpart");
    expect(weekend.filter).toEqual({ group: ["weekday:6", "weekday:7"] });
    // Every number is the summary's: the weekend's rows are the Saturdays' and
    // Sundays'.
    const weekdays = big.summary.groups.weekday.filter((e) =>
      ["6", "7"].includes(e.key),
    );
    const n = weekdays.reduce(
      (sum, e) => sum + tally(e, PURPOSE, PURPOSE_LABELS).answered,
      0,
    );
    expect(weekend.n).toBe(n);
  });

  it("is the same every time, and quick", () => {
    const run = () =>
      findPatterns({
        summary: big.summary,
        questions: QUESTIONS,
        focus: { configId: RESOLVED, answer: null },
      });
    const t0 = Date.now();
    const first = run();
    expect(Date.now() - t0).toBeLessThan(1000);
    expect(run().patterns).toEqual(first.patterns);
    expect(first.answer).toBe("No");
    expect(first.patterns.map((p) => p.text)).toEqual(
      expect.arrayContaining([
        expect.stringMatching(
          /^“No” when “What did the customer come for\?” was “Account or email change”/,
        ),
        expect.stringMatching(/^“No” for 40 users \(/),
      ]),
    );
  });

  it("says the counts are of the sample when it is one", () => {
    const sampled = bigSummary({ count: 1000, population: 104233, seed: 5 });
    const result = findPatterns({
      summary: sampled.summary,
      questions: QUESTIONS,
      focus: { configId: PURPOSE, answer: null },
    });
    expect(result.isSample).toBe(true);
    result.patterns.forEach((p) => expect(p.text).toMatch(/ in the sample/));
  });
});

describe("AskTimeline at scale", () => {
  it("draws each answer as one path across the columns, as shares or counts", () => {
    const { container } = renderViz(AskTimeline, { summary: big.summary });
    const svg = container.querySelector("svg[role='img']");
    expect(svg.querySelectorAll("*").length).toBeLessThan(200);
    // "No" stacks at the bottom, so its share reads against the baseline;
    // the rows Jev couldn't answer go on top, hatched.
    expect(
      [...svg.querySelectorAll("path[data-answer]")].map((p) =>
        p.getAttribute("data-answer"),
      ),
    ).toEqual(["No", "Yes", "__failed__"]);
    expect(
      svg.querySelector("path[data-answer='__failed__']").getAttribute("fill"),
    ).toMatch(/^url\(#failed-/);
    expect(
      screen.getByText(
        /^\d+-hour columns · \w+ \d+ – \w+ \d+ · drag to select$/,
      ),
    ).toBeInTheDocument();
    expect(svg).toHaveTextContent("100%");
    fireEvent.click(screen.getByRole("button", { name: "Count" }));
    expect(container.querySelector("svg[role='img']")).toHaveTextContent("1K");
    expect(container.querySelector("svg[role='img']")).not.toHaveTextContent(
      /\+\d/,
    );
  });

  it("shows the rows not answered yet as hollow, and selects a column as a time filter", () => {
    const running = bigSummary({ count: 20000, answered: 0.4, seed: 3 });
    const { container, onSelect } = renderViz(AskTimeline, {
      summary: running.summary,
      running: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Count" }));
    const pending = container.querySelector("path[data-answer='__pending__']");
    expect(pending.getAttribute("fill")).toBe("none");
    expect(pending.getAttribute("d").split("M").length - 1).toBeGreaterThan(50);
    expect(screen.getByText("Not answered yet")).toBeInTheDocument();
    const svg = container.querySelector("svg[role='img']");
    svg.focus();
    for (let i = 0; i < 40; i += 1)
      fireEvent.keyDown(svg, { key: "ArrowRight" });
    fireEvent.keyDown(svg, { key: "Enter" });
    const [filter, label] = onSelect.mock.calls[0];
    expect(Object.keys(filter).sort()).toEqual(["time_from", "time_to"]);
    expect(label).toMatch(/\d/);
  });

  it("lists a category question's answers by count, with their shares", () => {
    renderViz(AskTimeline, {
      summary: big.summary,
      focus: { configId: PURPOSE, answer: null },
    });
    const buttons = screen
      .getAllByRole("button")
      .filter(
        (b) =>
          b.hasAttribute("aria-pressed") &&
          !b.closest("[aria-label='Column height']"),
      );
    const counts = buttons.map((b) =>
      Number(b.textContent.match(/· ([\d,]+)$/)[1].replace(/,/g, "")),
    );
    expect(counts).toEqual([...counts].sort((a, b) => b - a));
    expect(buttons).toHaveLength(PURPOSE_LABELS.length);
    expect(buttons[0]).toHaveTextContent(/^Order status\d+% · /);
    // The answers stack largest first from the bottom, the catch-all in grey.
    const parts = [
      ...document.querySelectorAll("svg[role='img'] path[data-answer]"),
    ].map((p) => p.getAttribute("data-answer"));
    expect(parts.slice(0, PURPOSE_LABELS.length)).toEqual(
      buttons.map((b) => b.textContent.replace(/\d+% · [\d,]+$/, "")),
    );
  });
});

describe("AskHeatmap for a category question", () => {
  it("shows one small grid per answer by default, and selects that answer's rows in a cell", () => {
    const { container, onSelect } = renderViz(AskHeatmap, {
      records: small.records,
      window: small.window,
      focus: { configId: PURPOSE, answer: null },
    });
    const panels = [...container.querySelectorAll("svg[role='img']")];
    expect(panels).toHaveLength(PURPOSE_LABELS.length);
    PURPOSE_LABELS.forEach((label, i) =>
      expect(panels[i].getAttribute("aria-label")).toMatch(
        new RegExp(`^${label}: share of each`),
      ),
    );
    expect(screen.getByRole("combobox", { name: /Colour/ })).toHaveTextContent(
      "Each answer",
    );
    // Walk the Return or refund panel's cells until one holds a refund.
    const refund = panels[1];
    const days = 8;
    fireEvent.keyDown(refund, { key: "ArrowRight" });
    for (let r = 0; r < days && !onSelect.mock.calls.length; r += 1) {
      for (let c = 0; c < 8 && !onSelect.mock.calls.length; c += 1) {
        fireEvent.keyDown(refund, { key: "Enter" });
        fireEvent.keyDown(refund, { key: "ArrowRight" });
      }
      fireEvent.keyDown(refund, { key: "ArrowDown" });
      for (let c = 0; c < 8; c += 1)
        fireEvent.keyDown(refund, { key: "ArrowLeft" });
    }
    expect(onSelect).toHaveBeenCalledTimes(1);
    const [filter, label] = onSelect.mock.calls[0];
    expect(filter.config_id).toBe(PURPOSE);
    expect(filter.answer).toBe("Return or refund");
    expect(label).toMatch(/^“Return or refund” · /);
    const rows = small.records.filter((r) => matchesFilter(r, filter));
    expect(rows.length).toBeGreaterThan(0);
    rows.forEach((r) =>
      expect(answerState(r, PURPOSE).answer).toBe("Return or refund"),
    );
  });

  it("crosses the questions and outlines the cells that stand out", () => {
    renderViz(AskCrossTab, {
      summary: big.summary,
      focus: { configId: PURPOSE, answer: null },
    });
    const grid = screen.getByRole("grid");
    const outlined = within(grid)
      .getAllByRole("gridcell")
      .filter((cell) => cell.dataset.standsOut);
    // A Yes/No column says each finding once.
    expect(outlined.length).toBeGreaterThan(0);
    expect(
      outlined.some((cell) =>
        /^“What did the customer come for\?” was “Account or email change”: [\d,]+ of [\d,]+ answered “No”/.test(
          cell.getAttribute("aria-label"),
        ),
      ),
    ).toBe(true);
    // And names each one under the table, one line each.
    const notes = within(
      screen.getByRole("list", { name: "Outlined cells" }),
    ).getAllByRole("listitem");
    expect(notes).toHaveLength(outlined.length);
    expect(
      notes.some((li) =>
        /^Account or email change · “No”: \d+% vs \d+% in the other rows \([\d,]+ of [\d,]+\)$/.test(
          li.textContent,
        ),
      ),
    ).toBe(true);
  });
});

describe("AskPatterns for a category question", () => {
  it("looks at every answer and keys each callout by its colour", () => {
    const { onSelect } = renderViz(AskPatterns, {
      summary: big.summary,
      focus: { configId: PURPOSE, answer: null },
    });
    expect(
      screen.getByText(
        /Where any answer to “What did the customer come for\?”/,
      ),
    ).toBeInTheDocument();
    const item = screen.getByText(/at weekends/).closest("li");
    fireEvent.click(within(item).getByRole("button", { name: /^Show / }));
    // "Show" selects the rows that make the callout: weekend refunds.
    expect(
      within(item).getByRole("button", { name: /^Show / }),
    ).toHaveTextContent(/^Show [\d,]+$/);
    expect(onSelect).toHaveBeenCalledWith(
      {
        group: ["weekday:6", "weekday:7"],
        config_id: PURPOSE,
        answer: "Return or refund",
      },
      "“Return or refund” at weekends",
    );
  });
});

describe("answer colours", () => {
  it("keeps categories off Yes, No and failed, and greys a ninth label", () => {
    [...CATEGORY_COLORS, ...CATEGORY_COLORS_DARK].forEach((hex) =>
      expect([YES_COLOR, NO_COLOR, "#DB2F2D"]).not.toContain(hex),
    );
    const labels = Array.from({ length: 10 }, (_, i) => `L${i}`);
    const item = { answerType: "category", labels };
    expect(answerColor(item, "L0", theme)).toBe(CATEGORY_COLORS[0]);
    expect(answerColor(item, "L8", theme)).toBe(OTHER_CATEGORY_COLOR);
    const dark = createTheme({ palette: palette("dark"), typography });
    expect(answerColor(item, "L1", dark)).toBe(CATEGORY_COLORS_DARK[1]);
  });
});

describe("users the busiest 50 miss", () => {
  // The planted cohort: 40 users with 25 sessions each, never helped. On a
  // real run such users have fewer rows than the busiest 50 and sit in "other
  // users"; here they are taken out of the busiest to match.
  const { summary, run } = big;
  const cohort = new Set(run.cohort);
  const hidden = {
    ...summary,
    groups: {
      ...summary.groups,
      user: summary.groups.user.filter((entry) => !cohort.has(entry.label)),
    },
  };

  it("lists, for each answer, the 50 users who give it most clearly more", () => {
    const lists = summary.groups.user_by_answer;
    expect(Object.keys(lists).sort()).toEqual([PURPOSE, RESOLVED].sort());
    expect(Object.keys(lists[RESOLVED])).toEqual(["Yes", "No"]);
    const no = lists[RESOLVED].No;
    expect(no).toHaveLength(50);
    // Ranked by z = (k − a·p0) / √(a·p0·(1 − p0)) against the run's share of
    // "No", more rows first on a tie.
    const run = tally({ n: 0, counts: summary.counts }, RESOLVED, [
      "Yes",
      "No",
    ]);
    const p0 = run.byLabel.get("No") / run.answered;
    const surely = (entry) => {
      const t = tally(entry, RESOLVED, ["Yes", "No"]);
      return (
        (t.byLabel.get("No") - t.answered * p0) /
        Math.sqrt(t.answered * p0 * (1 - p0))
      );
    };
    no.forEach((entry, i) => {
      expect(entry.n).toBeGreaterThanOrEqual(3);
      if (i > 0) {
        const prev = no[i - 1];
        expect(
          surely(prev) > surely(entry) ||
            (surely(prev) === surely(entry) && prev.n >= entry.n),
        ).toBe(true);
      }
    });
    // Same entries as the busiest users': key, label, n, counts.
    expect(no[0]).toEqual(
      expect.objectContaining({
        key: expect.any(String),
        label: expect.any(String),
        n: expect.any(Number),
        counts: expect.any(Object),
      }),
    );
    // Every one of the cohort, first.
    expect(no.slice(0, 40).every((entry) => cohort.has(entry.label))).toBe(
      true,
    );
  });

  it("finds the cohort through them, as one callout", () => {
    expect(hidden.groups.user.some((entry) => cohort.has(entry.label))).toBe(
      false,
    );
    const result = findPatterns({
      summary: hidden,
      questions: QUESTIONS,
      focus: { configId: RESOLVED, answer: null },
    });
    const users = result.patterns.find(
      (p) => p.dimension === "users" && p.delta > 0,
    );
    // A user picked from up to 33,333 must clear that many comparisons: the
    // 34 with all 25 answered do; the 6 with 23 or 24 answered fall short.
    expect(users.text).toBe(
      "“No” for 34 users (cust_ava_100, cust_ben_101 and 32 more): 850 of 850 (100%) vs 45% elsewhere",
    );
    const names = new Map(
      hidden.groups.user_by_answer[RESOLVED].No.map((entry) => [
        `user:${entry.key}`,
        entry.label,
      ]),
    );
    users.filter.group.forEach((key) =>
      expect(cohort.has(names.get(key))).toBe(true),
    );
  });

  it("checks a picked user as if every user had been", () => {
    // Without the summary's busiest users, a user picked for having the
    // most "No" needs far stronger evidence than one of the busiest.
    const picked = findPatterns({
      summary: hidden,
      questions: QUESTIONS,
      focus: { configId: RESOLVED, answer: "No" },
      max: 50,
    });
    const busiest = findPatterns({
      summary,
      questions: QUESTIONS,
      focus: { configId: RESOLVED, answer: "No" },
      max: 50,
    });
    const usersOf = (r) =>
      [...r.patterns, ...r.more].find(
        (p) => p.dimension === "users" && p.delta > 0,
      );
    expect(usersOf(picked).q).toBeGreaterThan(usersOf(busiest).q * 1000);
  });
});

describe("a real categorical run", () => {
  // harbor-conversations, 108 sessions: what the customer came for (6
  // labels) and whether the agent resolved it.
  const [purpose, resolved] = real.questions;
  const summary = summarizeRecords({
    records: real.records,
    questions: real.questions,
    unit: "sessions",
  });

  it("outlines the answers that line up: refunds resolved, the rest not", () => {
    const matrix = buildMatrix({
      summary,
      questions: real.questions,
      question: purpose,
      pivot: "questions",
    });
    // 20 of 21 refunds resolved against about a third of the rest, and none
    // of the 10 "Something else" sessions.
    const refunds = matrix.standOut.get("Return or refund|Yes");
    expect(refunds).toMatchObject({ k: 20, n: 21 });
    expect(matrix.standOut.get("Something else|No")).toMatchObject({
      k: 10,
      n: 10,
    });
    // 7 of 9 account changes unresolved is not enough to stand out.
    expect(matrix.standOut.has("Account or email change|No")).toBe(false);
    // Only cells where a row has more of an answer are outlined.
    matrix.standOut.forEach((t) => expect(t.delta).toBeGreaterThan(0));
    expect(matrix.against.configId).toBe(resolved.configId);
  });

  it("calls out the outage's errors, all of them order-status sessions", () => {
    const result = findPatterns({
      summary,
      questions: real.questions,
      focus: { configId: purpose.configId, answer: null },
    });
    const error = result.patterns.find(
      (p) => p.dimension === "error" && p.label === "Order status",
    );
    expect(error).toBeTruthy();
    expect(error.k).toBe(8);
    expect(error.n).toBe(8);
    expect(error.filter).toEqual({ group: "error:error" });
  });

  it("draws the catch-all in grey, apart from the hues", () => {
    renderViz(AskHeatmap, {
      records: real.records,
      questions: real.questions,
      focus: { configId: purpose.configId, answer: null },
    });
    const panels = screen.getAllByRole("img", { name: /: share of each/ });
    expect(panels).toHaveLength(6);
    const color = (label) =>
      answerColor(
        { answerType: "category", labels: purpose.labels },
        label,
        theme,
      );
    expect(color("Something else")).toBe(CATCH_ALL_COLOR);
    expect(color("Order status")).toBe(CATEGORY_COLORS[0]);
    expect(isCatchAll("Other")).toBe(true);
    expect(isCatchAll("Order status")).toBe(false);
  });
});

describe("a model that changes on a day", () => {
  it("finds the deploy: gpt-4.1-mini resolving less since the day it started", () => {
    const result = findPatterns({
      summary: big.summary,
      questions: QUESTIONS,
      focus: { configId: RESOLVED, answer: null },
    });
    const since = result.patterns.find((p) => p.dimension === "modelsince");
    expect(since).toBeTruthy();
    // The fixture's deploy is five days before the run.
    const deploy = new Date(NOW);
    deploy.setDate(deploy.getDate() - 5);
    expect(since.text).toMatch(
      new RegExp(
        `^“No” for gpt-4\\.1-mini since ${deploy.toLocaleDateString("en-US", { month: "short", day: "numeric" })}: [\\d,]+ of [\\d,]+ \\(\\d+%\\) vs \\d+% before$`,
      ),
    );
    expect(since.rate).toBeGreaterThan(since.restRate + 0.2);
    // "Show" takes that model's days since, and only its "No" answers.
    expect(since.select.count).toBe(since.k);
    expect(since.select.filter.answer).toBe("No");
    since.select.filter.group.forEach((g) =>
      expect(g).toMatch(/^model_day:gpt-4\.1-mini\|\d{4}-\d{2}-\d{2}$/),
    );
  });

  it("draws Model × Day in the heatmap", () => {
    renderViz(AskHeatmap, {
      summary: big.summary,
      focus: { configId: RESOLVED, answer: "No" },
    });
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Rows/ }));
    fireEvent.click(screen.getByRole("option", { name: "Model × Day" }));
    const svg = screen.getByRole("img", {
      name: /^Share of “No” by model and day/,
    });
    expect(within(svg).getByText("gpt-4.1-mini")).toBeInTheDocument();
    expect(within(svg).getByText("All models, %")).toBeInTheDocument();
  });
});

describe("small multiples with few rows per cell", () => {
  it("hatches a cell with fewer than 3 answers and never shades it", () => {
    const { container } = renderViz(AskHeatmap, {
      records: real.records,
      questions: real.questions,
      focus: { configId: real.questions[0].configId, answer: null },
    });
    // Each panel draws its too-few cells with a hatch pattern.
    const hatched = container.querySelectorAll("svg pattern");
    expect(hatched.length).toBeGreaterThan(0);
    expect(
      screen.getByText(/Fewer than 3 answered: too few to shade/),
    ).toBeInTheDocument();
  });
});
