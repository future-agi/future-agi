import React from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import {
  AskCrossTab,
  AskHeatmap,
  AskPatterns,
  AskTimeline,
  matchesFilter,
} from "../index";
import { normalizeQuestion, runTally, summarizeRecords } from "../askSummary";
import {
  buildTimeline,
  columnBudget,
  rangeText,
  squareLayout,
  stackCap,
} from "../timelineBins";
import {
  OUTAGE_COUNT,
  OUTAGE_ERRORS,
  QUESTIONS,
  REPEAT_USER,
  RESOLVED,
  syntheticRun,
} from "./fixtures/syntheticRun";

vi.mock("src/components/iconify", () => ({ default: () => null }));

// The charts stop a window at "now"; pin it to the synthetic run's evening.
beforeAll(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 15, 18, 30));
});
afterAll(() => vi.useRealTimers());

const theme = createTheme({ palette: palette("light"), typography });
const run = syntheticRun();
const live = syntheticRun({ answered: 0.6 });

function renderViz(Component, props = {}) {
  const handlers = {
    onFocus: vi.fn(),
    onSelect: vi.fn(),
    onOpen: vi.fn(),
  };
  const view = render(
    <ThemeProvider theme={theme}>
      <Component
        records={run.records}
        questions={QUESTIONS}
        focus={{ configId: RESOLVED, answer: null }}
        window={run.window}
        running={false}
        {...handlers}
        {...props}
      />
    </ThemeProvider>,
  );
  return { ...view, ...handlers };
}

// The geometry AskTimeline draws at the 640px width jsdom falls back to.
// A grid's cells read out one by one with the keyboard, as a screen reader
// would: go(r, c) moves the cursor there and returns the words.
function cellReader(svg) {
  const status = svg.parentElement.querySelector("[role='status']");
  const at = { r: 0, c: 0 };
  fireEvent.keyDown(svg, { key: "ArrowRight" });
  return (r, c) => {
    const step = (key, axis, by) => {
      fireEvent.keyDown(svg, { key });
      at[axis] += by;
    };
    while (at.r < r) step("ArrowDown", "r", 1);
    while (at.r > r) step("ArrowUp", "r", -1);
    while (at.c < c) step("ArrowRight", "c", 1);
    while (at.c > c) step("ArrowLeft", "c", -1);
    return status.textContent;
  };
}

function geometryOf(records, window) {
  const timeline = buildTimeline({
    records,
    configId: RESOLVED,
    // Stacked as the timeline stacks a Yes/No question: "No" first.
    question: { ...QUESTIONS[0], labels: ["No", "Yes"] },
    window,
    maxColumns: columnBudget(records.length, 640),
  });
  const layout = squareLayout(
    640,
    timeline.columns.length,
    timeline.maxStack,
    stackCap(timeline.columns),
  );
  return { timeline, ...layout, baseY: 8 + layout.plotH };
}

describe("AskTimeline", () => {
  it("draws one square per row and counts each answer in the legend", () => {
    const { container } = renderViz(AskTimeline);
    expect(
      container.querySelectorAll("svg[role='img'] rect").length,
    ).toBeGreaterThanOrEqual(run.records.length);
    const legend = screen.getByRole("button", { name: /^No \d+% · \d+$/ });
    const no = run.records.filter(
      (r) => r.answers[RESOLVED].answer === "No",
    ).length;
    const share = Math.round((no / run.records.length) * 100);
    expect(legend).toHaveTextContent(`No${share}% · ${no}`);
    // About 100 rows across 640px: wide columns, so the squares are 12px.
    expect(
      screen.getByText(/One square per session · 4-hour columns/),
    ).toBeInTheDocument();
    expect(geometryOf(run.records, run.window).w).toBe(12);
  });

  it("marks the hours under each day once the days are wide enough", () => {
    const labels = (container) =>
      [...container.querySelectorAll("svg[role='img'] text")].map(
        (text) => text.textContent,
      );
    // A week across 640px: no room for hours.
    const week = renderViz(AskTimeline);
    expect(labels(week.container)).not.toContain("6a");
    week.unmount();
    const since = new Date(2026, 9, 14).getTime();
    const twoDays = run.records.filter((r) => new Date(r.time) >= since);
    const { container } = renderViz(AskTimeline, {
      records: twoDays,
      window: [new Date(since).toISOString(), run.window[1]],
    });
    expect(labels(container)).toEqual(
      expect.arrayContaining(["Wed, Oct 14", "3a", "6a", "12p", "3p", "6p"]),
    );
  });

  it("focuses an answer from the legend and clears it on a second click", () => {
    const first = renderViz(AskTimeline);
    fireEvent.click(screen.getByRole("button", { name: /^No \d+% · \d+$/ }));
    expect(first.onFocus).toHaveBeenCalledWith({
      configId: RESOLVED,
      answer: "No",
    });
    first.unmount();
    const focused = renderViz(AskTimeline, {
      focus: { configId: RESOLVED, answer: "No" },
    });
    const button = screen.getByRole("button", { name: /^No \d+% · \d+$/ });
    expect(button).toHaveAttribute("aria-pressed", "true");
    // Every square that is not "No" is dimmed.
    const dim = [
      ...focused.container.querySelectorAll("svg rect[opacity='0.18']"),
    ];
    const yes = run.records.filter(
      (r) => r.answers[RESOLVED].answer === "Yes",
    ).length;
    expect(dim).toHaveLength(yes);
    fireEvent.click(button);
    expect(focused.onFocus).toHaveBeenCalledWith({
      configId: RESOLVED,
      answer: null,
    });
  });

  it("selects the time range dragged across", () => {
    const { container, onSelect } = renderViz(AskTimeline);
    const { timeline, colW, baseY } = geometryOf(run.records, run.window);
    const at = new Date(run.outage.day).setHours(14, 30);
    const from = timeline.columns.findIndex((c) => c.start <= at && at < c.end);
    const svg = container.querySelector("svg[role='img']");
    fireEvent.mouseDown(svg, {
      button: 0,
      clientX: from * colW + 1,
      clientY: baseY - 2,
    });
    fireEvent.mouseMove(svg, {
      clientX: (from + 1) * colW + 3,
      clientY: baseY - 2,
    });
    fireEvent.mouseUp(svg, {
      clientX: (from + 1) * colW + 3,
      clientY: baseY - 2,
    });
    expect(onSelect).toHaveBeenCalledTimes(1);
    const [filter, label] = onSelect.mock.calls[0];
    expect(filter).toEqual({
      time_from: new Date(timeline.columns[from].start).toISOString(),
      time_to: new Date(timeline.columns[from + 1].end).toISOString(),
    });
    const ids = run.records
      .filter((r) => matchesFilter(r, filter))
      .map((r) => r.id);
    run.outage.ids.forEach((id) => expect(ids).toContain(id));
    expect(label).toBe(
      rangeText(timeline.columns[from].start, timeline.columns[from + 1].end),
    );
    expect(label).toMatch(/PM – \d+ PM$/);
  });

  it("opens the row of a clicked square and selects a column from the keyboard", () => {
    const { container, onOpen, onSelect } = renderViz(AskTimeline);
    const { timeline, colW, pitch, baseY } = geometryOf(
      run.records,
      run.window,
    );
    const col = timeline.columns.findIndex((c) => c.items.length);
    const svg = container.querySelector("svg[role='img']");
    const point = {
      button: 0,
      clientX: col * colW + colW / 2,
      clientY: baseY - pitch / 2,
    };
    fireEvent.mouseDown(svg, point);
    fireEvent.mouseUp(svg, point);
    expect(onOpen).toHaveBeenCalledWith(timeline.columns[col].items[0].id);
    // The keyboard cursor starts at the first column; walk to a full one.
    for (let step = 0; step <= col; step += 1)
      fireEvent.keyDown(svg, { key: "ArrowRight" });
    fireEvent.keyDown(svg, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith(
      {
        time_from: new Date(timeline.columns[col].start).toISOString(),
        time_to: new Date(timeline.columns[col].end).toISOString(),
      },
      expect.any(String),
    );
  });

  it("shows rows being read and rows not reached while it runs", () => {
    const { container } = renderViz(AskTimeline, {
      records: live.records,
      window: live.window,
      running: true,
    });
    expect(container.querySelectorAll("rect.reading")).toHaveLength(6);
    expect(screen.getByText("Reading")).toBeInTheDocument();
    expect(screen.getByText("Not yet")).toBeInTheDocument();
  });

  it("counts its legend from the summary the page reads while the squares lag", () => {
    // The squares are read again every twentieth of the run; the summary
    // every poll. Its newer counts are the ones the header shows.
    const newer = summarizeRecords({
      records: syntheticRun({ answered: 0.8 }).records,
      questions: QUESTIONS,
      window: live.window,
      status: "running",
    });
    const tallied = runTally(newer, normalizeQuestion(QUESTIONS[0]));
    renderViz(AskTimeline, {
      summary: newer,
      records: live.records,
      window: live.window,
      running: true,
    });
    const legendCount = (label) =>
      Number(
        screen
          .getByText(label)
          .parentElement.textContent.slice(label.length)
          .replace(/,/g, ""),
      );
    const reading = legendCount("Reading");
    expect(reading).toBeGreaterThan(0);
    expect(reading + legendCount("Not yet")).toBe(tallied.pending);
  });

  it("marks the columns whose rows had a trace error", () => {
    const { container } = renderViz(AskTimeline);
    const marks = [...container.querySelectorAll("svg[role='img'] rect")]
      .map((rect) => rect.querySelector("title")?.textContent)
      .filter(Boolean);
    expect(marks.length).toBeGreaterThan(0);
    const total = marks.reduce(
      (sum, text) => sum + Number(text.match(/^(\d+) had a trace error$/)[1]),
      0,
    );
    expect(total).toBe(OUTAGE_ERRORS);
    expect(
      screen.getByText("Trace had an error").parentElement,
    ).toHaveTextContent(`Trace had an error${OUTAGE_ERRORS}`);
  });

  it("cuts a burst column at the cap with +N, a summary and a click that selects it", () => {
    // 30 more sessions in one hour: that column alone would set the scale.
    const burst = Array.from({ length: 30 }, (_, i) => ({
      ...run.records[i % run.records.length],
      id: `burst-${i}`,
      time: new Date(2026, 9, 14, 9, i * 2).toISOString(),
    }));
    const records = [...run.records, ...burst];
    const { container, onSelect, onOpen } = renderViz(AskTimeline, {
      records,
    });
    const { timeline, colW, baseY, cap, capped, w } = geometryOf(
      records,
      run.window,
    );
    expect(capped).toBe(true);
    expect(cap).toBe(12);
    expect(w).toBeGreaterThanOrEqual(10);
    const at = new Date(2026, 9, 14, 9, 30).getTime();
    const col = timeline.columns.findIndex((c) => c.start <= at && at < c.end);
    const column = timeline.columns[col];
    expect(column.items.length).toBeGreaterThan(cap);
    const svg = container.querySelector("svg[role='img']");
    expect(svg).toHaveTextContent(`+${column.items.length - cap}`);
    // The chart is scaled to the cap, not to the burst.
    expect(Number(svg.getAttribute("height"))).toBeLessThanOrEqual(
      8 + 220 + 40,
    );
    const point = { clientX: col * colW + colW / 2, clientY: baseY - 2 };
    fireEvent.mouseMove(svg, point);
    const no = column.items.filter((item) => item.answer === "No").length;
    const yes = column.items.filter((item) => item.answer === "Yes").length;
    const [more, fewer] =
      no >= yes ? [`${no} No`, `${yes} Yes`] : [`${yes} Yes`, `${no} No`];
    expect(
      screen.getByText(`${column.items.length} sessions · ${more} · ${fewer}`),
    ).toBeInTheDocument();
    fireEvent.mouseDown(svg, { button: 0, ...point });
    fireEvent.mouseUp(svg, point);
    expect(onOpen).not.toHaveBeenCalled();
    const [filter, label] = onSelect.mock.calls[0];
    const ids = records
      .filter((r) => matchesFilter(r, filter))
      .map((r) => r.id);
    expect(new Set(ids)).toEqual(new Set(column.items.map((item) => item.id)));
    expect(label).toBe(rangeText(column.start, column.end));
  });

  it("turns rows into thin slices only when even cut columns would be tiny", () => {
    // 25 sessions every 4 hours and one 400-session burst (1,300 rows, still
    // one square each): cut at 38, the squares would be under 6px, so every
    // row is a slice and none is cut.
    const dense = [];
    for (let hour = 0; hour < 6 * 24; hour += 4)
      for (let i = 0; i < 25; i += 1)
        dense.push({
          ...run.records[i % run.records.length],
          id: `dense-${hour}-${i}`,
          time: new Date(2026, 9, 9, hour, i).toISOString(),
        });
    const burst = Array.from({ length: 400 }, (_, i) => ({
      ...run.records[i % run.records.length],
      id: `burst-${i}`,
      time: new Date(2026, 9, 14, 9, i % 60).toISOString(),
    }));
    const { container } = renderViz(AskTimeline, {
      records: [...dense, ...burst],
    });
    const svg = container.querySelector("svg[role='img']");
    expect(Number(svg.getAttribute("height"))).toBeLessThanOrEqual(
      8 + 220 + 40,
    );
    expect(svg).not.toHaveTextContent(/\+\d+/);
    const slices = [...svg.querySelectorAll("rect")].filter(
      (rect) => Number(rect.getAttribute("height")) < 1,
    );
    expect(slices.length).toBeGreaterThanOrEqual(400);
  });

  it("switches the question it colours by", () => {
    const { onFocus } = renderViz(AskTimeline);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Colour by/ }));
    fireEvent.click(
      screen.getByRole("option", { name: QUESTIONS[1].question }),
    );
    expect(onFocus).toHaveBeenCalledWith({
      configId: QUESTIONS[1].configId,
      answer: null,
    });
  });
});

describe("AskPatterns", () => {
  it("calls out the outage afternoon with its counts and selects it", () => {
    const { onSelect } = renderViz(AskPatterns);
    const item = screen.getByText(/2–5 PM/).closest("li");
    expect(item).toHaveTextContent(
      `${OUTAGE_COUNT} of ${OUTAGE_COUNT} (100%) vs`,
    );
    fireEvent.click(
      within(item).getByRole("button", { name: `Show ${OUTAGE_COUNT}` }),
    );
    const [filter, label] = onSelect.mock.calls[0];
    const ids = run.records
      .filter((r) => matchesFilter(r, filter))
      .map((r) => r.id);
    expect(new Set(ids)).toEqual(new Set(run.outage.ids));
    expect(label).toMatch(/^“No” on \w+ \d+, 2–5 PM$/);
  });

  it("waits for enough answers, then says plainly when nothing stands out", () => {
    const early = syntheticRun({ answered: 0.05 });
    const first = renderViz(AskPatterns, {
      records: early.records,
      running: true,
    });
    expect(
      screen.getByText(/Patterns appear once 12 rows are answered/),
    ).toBeInTheDocument();
    first.unmount();
    const flat = run.records.map((record, i) => ({
      ...record,
      facets: {},
      user_id: "",
      end_user_id: "",
      answers: {
        [RESOLVED]: { answer: i % 2 ? "Yes" : "No", status: "completed" },
      },
    }));
    renderViz(AskPatterns, { records: flat, questions: [QUESTIONS[0]] });
    expect(screen.getByText(/^Nothing stands out: no day/)).toBeInTheDocument();
  });

  it("counts the answers so far as they land, and finds patterns once there are enough", () => {
    // Runs 9e0df0dd and feb70a5c: "(0 so far)" stayed while 11 and then 21
    // rows were answered, held by the 10-second recheck.
    const answeredFirst = (count) =>
      run.records.map((record, i) =>
        i < count ? record : { ...record, answers: {} },
      );
    const view = renderViz(AskPatterns, {
      records: answeredFirst(0),
      running: true,
    });
    const rerender = (count) =>
      view.rerender(
        <ThemeProvider theme={theme}>
          <AskPatterns
            records={answeredFirst(count)}
            questions={QUESTIONS}
            focus={{ configId: RESOLVED, answer: null }}
            window={run.window}
            running
          />
        </ThemeProvider>,
      );
    rerender(3);
    rerender(7);
    expect(
      screen.getByText("Patterns appear once 12 rows are answered (7 so far)."),
    ).toBeInTheDocument();
    rerender(11);
    expect(screen.getByText(/\(11 so far\)/)).toBeInTheDocument();
    rerender(run.records.length);
    expect(screen.queryByText(/Patterns appear once/)).toBeNull();
    expect(screen.getByText(/2–5 PM/)).toBeInTheDocument();
  });

  it("states the share, not a finding, when nothing stands out at 12 answers", () => {
    // 12 answered, 8 "No": the run 97cbf545 on harbor-conversations.
    const twelve = run.records.slice(0, 12).map((record, i) => ({
      ...record,
      facets: {},
      user_id: "",
      end_user_id: "",
      answers: {
        [RESOLVED]: { answer: i % 3 ? "No" : "Yes", status: "completed" },
      },
    }));
    renderViz(AskPatterns, {
      records: twelve,
      questions: [QUESTIONS[0]],
      focus: { configId: RESOLVED, answer: "No" },
    });
    expect(screen.getByText(/^Nothing stands out: no day/)).toBeInTheDocument();
    expect(screen.queryByText(/clearly more or less common/)).toBeNull();
    expect(screen.getByText(/: 67% of 12 answered\./)).toBeInTheDocument();
  });

  it("says when every row of a callout fell in one afternoon", () => {
    // Every outage session, and only those, hit an error.
    const outage = new Set(run.outage.ids);
    renderViz(AskPatterns, {
      records: run.records.map((record) => ({
        ...record,
        facets: { ...record.facets, error: outage.has(record.id) },
      })),
    });
    const item = screen.getByText(/when the trace had an error/).closest("li");
    expect(item).toHaveTextContent(
      new RegExp(`elsewhere, all ${OUTAGE_COUNT} on \\w+ \\d+, 2–5 PM`),
    );
    expect(screen.getByText(/comparisons: \d+ checked/)).toBeInTheDocument();
  });

  it("switches the answer it looks at", () => {
    const { onFocus } = renderViz(AskPatterns);
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect(onFocus).toHaveBeenCalledWith({ configId: RESOLVED, answer: "Yes" });
  });
});

describe("AskHeatmap", () => {
  it("replaces sparse time cells with a read-more state, without the pivot controls", () => {
    const records = run.records.slice(0, 5).map((record, i) => ({
      ...record,
      time: new Date(2026, 8, 20 + i * 5, 10).toISOString(),
    }));
    const onReadMore = vi.fn();
    renderViz(AskHeatmap, {
      records,
      window: [
        new Date(2026, 8, 15).toISOString(),
        new Date(2026, 9, 16).toISOString(),
      ],
      onReadMore,
    });
    const card = screen.getByRole("region", { name: "Heatmap of answers" });
    expect(
      within(card).getByText("Not enough answers for time patterns"),
    ).toBeInTheDocument();
    expect(within(card).queryByRole("img")).toBeNull();
    expect(within(card).queryByRole("combobox")).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "Read more" }));
    expect(onReadMore).toHaveBeenCalledTimes(1);
  });

  it("keeps the heatmap once a populated cell has three answers", () => {
    const records = run.records.slice(0, 3).map((record) => ({
      ...record,
      time: new Date(2026, 9, 13, 10).toISOString(),
    }));
    renderViz(AskHeatmap, { records });
    expect(
      screen.queryByText("Not enough answers for time patterns"),
    ).toBeNull();
    expect(screen.getByRole("combobox", { name: "Rows" })).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /^Share of “No” by day/ }),
    ).toBeInTheDocument();
  });

  it("shows Day × Hour and selects a cell's rows", () => {
    const { onSelect } = renderViz(AskHeatmap);
    const svg = screen.getByRole("img", {
      name: /^Share of “No” by day and hour/,
    });
    // A week of hours in a few paths, not an element per cell.
    expect(svg.querySelectorAll("rect").length).toBeLessThan(5);
    const go = cellReader(svg);
    const first = new Date(run.window[0]);
    const row = Math.round(
      (new Date(run.outage.day).setHours(12) -
        new Date(first.getFullYear(), first.getMonth(), first.getDate(), 12)) /
        (24 * 60 * 60 * 1000),
    );
    expect(go(row, 14)).toMatch(
      /2 PM–3 PM: \d+ of \d+ “No” \(100%\) vs \d+% overall/,
    );
    fireEvent.keyDown(svg, { key: "Enter" });
    const [filter, label] = onSelect.mock.calls[0];
    const ids = run.records
      .filter((r) => matchesFilter(r, filter))
      .map((r) => r.id);
    expect(ids.length).toBeGreaterThan(0);
    ids.forEach((id) => expect(run.outage.ids).toContain(id));
    expect(label).toMatch(/2 PM–3 PM$/);
  });

  it("totals each hour along the bottom", () => {
    renderViz(AskHeatmap);
    const svg = screen.getByRole("img", {
      name: /^Share of “No” by day and hour/,
    });
    const go = cellReader(svg);
    // The row of totals sits under the 7 days.
    let answered = 0;
    for (let c = 0; c < 24; c += 1) {
      const found = go(7, c).match(/: \d+ of (\d+) “No”/);
      answered += found ? Number(found[1]) : 0;
    }
    expect(answered).toBe(run.records.length);
    expect(within(svg).getByText("All days, %")).toBeInTheDocument();
  });

  it("dots the cells with rows that had a trace error", () => {
    renderViz(AskHeatmap);
    const svg = screen.getByRole("img", {
      name: /^Share of “No” by day and hour/,
    });
    const errored = new Set(
      run.records
        .filter((r) => r.facets.error)
        .map((r) => {
          const t = new Date(r.time);
          return `${t.toDateString()}|${t.getHours()}`;
        }),
    );
    const dots = svg.querySelector("path[data-errors]");
    expect(dots.getAttribute("d").split("m-2.5,0").length - 1).toBe(
      errored.size,
    );
    expect(screen.getByText("Trace had an error")).toBeInTheDocument();
  });

  it("lists the busiest users by answer", () => {
    renderViz(AskHeatmap);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Rows/ }));
    fireEvent.click(screen.getByRole("option", { name: "Users" }));
    expect(
      screen.getByText(
        /The 15 busiest; \d+ more sessions are in the comparison/,
      ),
    ).toBeInTheDocument();
    // The customer who never gets an answer is outlined in "No".
    const outlined = within(screen.getByRole("grid"))
      .getAllByRole("gridcell")
      .filter((cell) => cell.dataset.standsOut);
    expect(
      outlined.some((cell) =>
        cell.getAttribute("aria-label").startsWith(`${REPEAT_USER}:`),
      ),
    ).toBe(true);
  });

  it("lines one question's answers up against another's in their own card", () => {
    renderViz(AskCrossTab);
    expect(
      screen.getByRole("region", { name: "Question against question" }),
    ).toBeInTheDocument();
    const cells = within(screen.getByRole("grid")).getAllByRole("gridcell");
    expect(cells).toHaveLength(2 * QUESTIONS[1].labels.length);
    expect(cells[0].getAttribute("aria-label")).toMatch(
      /^“Did the agent resolve the customer's issue\?” was “Yes”: \d+ of \d+ answered “/,
    );
  });
});
