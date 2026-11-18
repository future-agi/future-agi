import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  within,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { AskCrossTab, AskPatterns } from "../index";
import { findPatterns } from "../askStats";
import { buildMatrix } from "../askPivot";
import { chartQuestions, parseDayKey } from "../askSummary";
import { useThrottled } from "../vizParts";
import { NO_COLOR } from "../../../results/answerColors";
import summary from "./fixtures/real100k";

// The real 100,000-trace run on harbor-scale (A's seeded month): an outage on
// Oct 7, 2–5 PM; gpt-4.1-mini resolving half as often after a deploy on
// Oct 11; 40 users with many sessions and a 10% resolve rate; and more
// refunds at weekends. The fixture is the server's summary, counts only.

vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light"), typography });
const questions = chartQuestions([], summary);
const purpose = questions.find((q) => q.answerType === "category");
const resolved = questions.find((q) => q !== purpose);

// The cohort, from the seeding's ground truth.
const COHORT = [
  "cust_aisha_67275",
  "cust_amara_11546",
  "cust_andre_36220",
  "cust_carmen_79136",
  "cust_chen_96451",
  "cust_daria_5232",
  "cust_daria_99238",
  "cust_elena_16750",
  "cust_elena_61156",
  "cust_elena_84832",
  "cust_fatima_42823",
  "cust_fatima_67656",
  "cust_felix_37234",
  "cust_isaac_67374",
  "cust_isla_11630",
  "cust_isla_77460",
  "cust_isla_97924",
  "cust_jonas_31568",
  "cust_jonas_70683",
  "cust_jordan_73646",
  "cust_jordan_89990",
  "cust_lucas_37145",
  "cust_lucia_40678",
  "cust_malik_48548",
  "cust_malik_65404",
  "cust_marcus_33979",
  "cust_noah_44667",
  "cust_nora_37826",
  "cust_olivia_56657",
  "cust_omar_23664",
  "cust_priya_6205",
  "cust_ravi_84149",
  "cust_rosa_45510",
  "cust_ruby_16718",
  "cust_sofia_30504",
  "cust_sven_85769",
  "cust_theo_40564",
  "cust_tomas_30270",
  "cust_vera_24642",
  "cust_zoe_13133",
];

const patternsFor = (question) =>
  findPatterns({
    summary,
    questions,
    focus: { configId: question.configId, answer: null },
    unit: "traces",
  });

const isWeekend = (day) => [0, 6].includes(parseDayKey(day).getDay());
const dayOfWindow = (pattern) => pattern.key.split("|window:")[1]?.slice(0, 10);

function renderViz(Component, props = {}) {
  const handlers = { onFocus: vi.fn(), onSelect: vi.fn(), onOpen: vi.fn() };
  const view = render(
    <ThemeProvider theme={theme}>
      <Component
        summary={summary}
        questions={questions}
        running={false}
        unit="traces"
        {...handlers}
        {...props}
      />
    </ThemeProvider>,
  );
  return { ...view, ...handlers };
}

describe("What stands out on the real 100k run: did the agent resolve it?", () => {
  const result = patternsFor(resolved);
  const texts = result.patterns.map((p) => p.text);

  it("shows the regression, the errors and the outage", () => {
    expect(result.answer).toBe("No");
    expect(texts).toEqual(
      expect.arrayContaining([
        "“No” for gpt-4.1-mini since Oct 11: 5789 of 9417 (61%) vs 36% before",
        "“No” when the trace had an error: 1259 of 1282 (98%) vs 37% elsewhere",
        "“No” on Oct 7, 2–5 PM: 1670 of 1952 (86%) vs 37% elsewhere",
      ]),
    );
  });

  // The cohort's users have 46–91 traces each, fewer than the 50 busiest
  // (111–139): they show through `user_by_answer`, each answer's 50 users
  // with the highest z-score against the run's share.
  it("shows the cohort that is rarely helped, as one callout", () => {
    const names = new Map(
      [
        ...summary.groups.user,
        ...Object.values(
          summary.groups.user_by_answer[resolved.configId],
        ).flat(),
      ].map((entry) => [`user:${entry.key}`, entry.label]),
    );
    const cohort = result.patterns.find(
      (p) => p.dimension === "users" && p.delta > 0,
    );
    expect(cohort.text).toBe(
      "“No” for 42 users (cust_noah_44667, cust_andre_36220 and 40 more): 2352 of 2839 (83%) vs 36% elsewhere",
    );
    // All 40 of the cohort, and two users who fare as badly.
    const members = cohort.filter.group.map((key) => names.get(key));
    expect(members.filter((name) => COHORT.includes(name))).toHaveLength(40);
    // Four busy users at about half "No" are a second, separate callout.
    const busy = result.more.find(
      (p) => p.dimension === "users" && p.delta > 0,
    );
    expect(busy.text).toMatch(/^“No” for 4 users \(.*\): 252 of 482 \(52%\)/);
    busy.filter.group.forEach((key) =>
      expect(COHORT).not.toContain(names.get(key)),
    );
  });

  it("puts the explanations before the hours they explain", () => {
    // Model, errors and users come first; the outage's afternoon follows.
    const order = result.patterns.map((p) => p.family);
    expect(order.indexOf("time")).toBeGreaterThan(order.indexOf("model"));
    expect(order.indexOf("time")).toBeGreaterThan(order.indexOf("error"));
    // At most two of a kind, five in all.
    expect(result.patterns.length).toBe(5);
    const families = new Map();
    order.forEach((f) => families.set(f, (families.get(f) || 0) + 1));
    families.forEach((count) => expect(count).toBeLessThanOrEqual(2));
  });

  it("moves the afternoons after Oct 11 under the model's change", () => {
    // No stretch of a day on or after the deploy is shown on its own when it
    // says what the model's change says.
    result.patterns.forEach((p) => {
      const day = dayOfWindow(p);
      if (day && p.delta > 0) expect(day < "2026-10-11").toBe(true);
    });
    const after = result.more.filter(
      (p) => dayOfWindow(p) >= "2026-10-11" && p.delta > 0,
    );
    expect(after.length).toBeGreaterThan(10);
    after.forEach((p) =>
      expect(p.within?.where).toBe("for gpt-4.1-mini since Oct 11"),
    );
    // Nothing is lost: every one that passed is under "Show all".
    expect(result.patterns.length + result.more.length).toBe(
      new Set([...result.patterns, ...result.more].map((p) => p.key)).size,
    );
  });
});

describe("What stands out on the real 100k run: what did the customer come for?", () => {
  const result = patternsFor(purpose);
  const texts = result.patterns.map((p) => p.text);

  it("shows weekend refunds and order status around the outage", () => {
    expect(result.answer).toBe(null);
    expect(texts).toEqual(
      expect.arrayContaining([
        "“Return or refund” at weekends: 9574 of 24135 (40%) vs 21% elsewhere",
        "“Order status” when the trace had an error: 688 of 1278 (54%) vs 31% elsewhere",
        "“Order status” on Oct 7, 2–5 PM: 883 of 1953 (45%) vs 31% elsewhere",
      ]),
    );
    expect(result.patterns.length).toBe(5);
  });

  it("shows no weekend afternoon on its own: they are the weekend again", () => {
    const refundWindows = (list) =>
      list.filter(
        (p) =>
          p.label === "Return or refund" &&
          p.delta > 0 &&
          dayOfWindow(p) &&
          isWeekend(dayOfWindow(p)),
      );
    expect(refundWindows(result.patterns)).toEqual([]);
    const nested = refundWindows(result.more).filter((p) => p.within);
    expect(nested.length).toBeGreaterThan(20);
    nested.forEach((p) => expect(p.within.where).toBe("at weekends"));
  });

  it("leads with the outage's errors and folds their complement under them", () => {
    // Product advice is missing from errored traces, so it is more common
    // in the rest: said that way round, and the same finding as the errors'
    // order status, seen from the other side.
    const all = [...result.patterns, ...result.more];
    const errors = all.find((p) =>
      p.text.startsWith("“Order status” when the trace had an error:"),
    );
    const complement = all.find((p) =>
      p.text.startsWith("“Product advice” when the trace had no error:"),
    );
    expect(complement.text).toMatch(/\(16%\) vs 0% elsewhere$/);
    expect(
      all.some((p) =>
        p.text.startsWith("“Product advice” when the trace had an error"),
      ),
    ).toBe(false);
    // It accounts for many more rows, so it would rank first on its own.
    const excess = (p) => Math.abs(p.k - p.n * p.restRate);
    expect(excess(complement)).toBeGreaterThan(excess(errors));
    expect(result.patterns).toContain(errors);
    expect(result.patterns).not.toContain(complement);
    expect(complement.within).toEqual({
      key: errors.key,
      label: "Order status",
      where: "when the trace had an error",
    });
    expect(all.indexOf(complement)).toBeGreaterThan(all.indexOf(errors));
    // Every callout on the no-error side sits under the errors' one.
    all
      .filter((p) => p.where === "when the trace had no error")
      .forEach((p) => expect(p.within?.key).toBe(errors.key));
  });
});

describe("the real 100k run's questions against each other", () => {
  it("outlines account changes left unresolved, 47% against 37%", () => {
    const matrix = buildMatrix({
      summary,
      questions,
      question: purpose,
      pivot: "questions",
    });
    const cell = matrix.standOut.get("Account or email change|No");
    expect(cell).toMatchObject({ k: 2984, n: 6399 });
    expect(cell.rate).toBeCloseTo(0.466, 3);
    expect(cell.restRate).toBeCloseTo(0.37, 2);
    // A 2–3 point gap at this size is real but not worth an outline.
    expect(matrix.standOut.has("Order status|Yes")).toBe(false);
    expect(matrix.standOut.has("Return or refund|No")).toBe(false);
  });

  it("draws the outline in the answer's hue and names it below", () => {
    renderViz(AskCrossTab, {
      focus: { configId: purpose.configId, answer: null },
    });
    const cell = within(screen.getByRole("grid"))
      .getAllByRole("gridcell")
      .find((c) =>
        /was “Account or email change”: 2,984 of 6,399 answered “No”/.test(
          c.getAttribute("aria-label"),
        ),
      );
    expect(cell.dataset.standsOut).toBe("true");
    expect(cell.style.boxShadow.toLowerCase()).toBe(
      `inset 0 0 0 2px ${NO_COLOR.toLowerCase()}`,
    );
    const notes = within(
      screen.getByRole("list", { name: "Outlined cells" }),
    ).getAllByRole("listitem");
    expect(notes.map((li) => li.textContent)).toContain(
      "Account or email change · “No”: 47% vs 37% in the other rows (2,984 of 6,399)",
    );
  });
});

describe("AskPatterns on the real 100k run", () => {
  it("shows five, and every other one under Show all", () => {
    const result = patternsFor(purpose);
    renderViz(AskPatterns, {
      focus: { configId: purpose.configId, answer: null },
    });
    const top = screen.getByRole("list", { name: "Strongest patterns" });
    expect(within(top).getAllByRole("listitem")).toHaveLength(5);
    const total = result.patterns.length + result.more.length;
    fireEvent.click(
      screen.getByRole("button", {
        name: `Show all ${total.toLocaleString()}`,
      }),
    );
    const more = screen.getByRole("list", { name: "More patterns" });
    const rows = within(more).getAllByRole("listitem");
    expect(rows).toHaveLength(Math.min(50, result.more.length));
    expect(
      rows.some((li) =>
        /· part of “Return or refund” at weekendsShow [\d,]+$/.test(
          li.textContent,
        ),
      ),
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Top 5 only" }));
    expect(screen.queryByRole("list", { name: "More patterns" })).toBeNull();
  });
});

describe("recomputing patterns while answers arrive", () => {
  afterEach(() => vi.useRealTimers());

  it("takes a new value at most every 10 seconds, and at once for a new focus", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ value, ms, focusKey }) => useThrottled(value, ms, focusKey),
      { initialProps: { value: 1, ms: 10000, focusKey: "a" } },
    );
    rerender({ value: 2, ms: 10000, focusKey: "a" });
    expect(result.current).toBe(2);
    rerender({ value: 3, ms: 10000, focusKey: "a" });
    act(() => vi.advanceTimersByTime(4000));
    rerender({ value: 4, ms: 10000, focusKey: "a" });
    expect(result.current).toBe(2);
    act(() => vi.advanceTimersByTime(6000));
    expect(result.current).toBe(4);
    rerender({ value: 5, ms: 10000, focusKey: "a" });
    expect(result.current).toBe(4);
    // A new answer in focus recomputes now, with the latest counts.
    rerender({ value: 5, ms: 10000, focusKey: "b" });
    expect(result.current).toBe(5);
    // Once the run is done, every value is taken.
    rerender({ value: 6, ms: 0, focusKey: "b" });
    expect(result.current).toBe(6);
  });
});
