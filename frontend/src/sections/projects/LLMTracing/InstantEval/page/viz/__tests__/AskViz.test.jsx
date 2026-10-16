import React from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { AskHeatmap, AskPatterns, AskTimeline } from "../index";
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
function geometryOf(records, window) {
  const timeline = buildTimeline({
    records,
    configId: RESOLVED,
    question: QUESTIONS[0],
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
    const legend = screen.getByRole("button", { name: /^No \d+$/ });
    const no = run.records.filter(
      (r) => r.answers[RESOLVED].answer === "No",
    ).length;
    expect(legend).toHaveTextContent(`No${no}`);
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
    fireEvent.click(screen.getByRole("button", { name: /^No \d+$/ }));
    expect(first.onFocus).toHaveBeenCalledWith({
      configId: RESOLVED,
      answer: "No",
    });
    first.unmount();
    const focused = renderViz(AskTimeline, {
      focus: { configId: RESOLVED, answer: "No" },
    });
    const button = screen.getByRole("button", { name: /^No \d+$/ });
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
    const [ids, label] = onSelect.mock.calls[0];
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
      timeline.columns[col].items.map((item) => item.id),
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

  it("marks the columns whose rows hit an error", () => {
    const { container } = renderViz(AskTimeline);
    const marks = [...container.querySelectorAll("svg[role='img'] rect")]
      .map((rect) => rect.querySelector("title")?.textContent)
      .filter(Boolean);
    expect(marks.length).toBeGreaterThan(0);
    const total = marks.reduce(
      (sum, text) => sum + Number(text.match(/^(\d+) hit an error$/)[1]),
      0,
    );
    expect(total).toBe(OUTAGE_ERRORS);
    expect(
      screen.getByText("Run hit an error").parentElement,
    ).toHaveTextContent(`Run hit an error${OUTAGE_ERRORS}`);
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
    const [ids, label] = onSelect.mock.calls[0];
    expect(new Set(ids)).toEqual(new Set(column.items.map((item) => item.id)));
    expect(label).toBe(rangeText(column.start, column.end));
  });

  it("turns rows into thin slices only when even cut columns would be tiny", () => {
    // 50 sessions every 4 hours and one 400-session burst: cut at 75, the
    // squares would be under 6px, so every row is a slice and none is cut.
    const dense = [];
    for (let hour = 0; hour < 6 * 24; hour += 4)
      for (let i = 0; i < 50; i += 1)
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
    const [ids, label] = onSelect.mock.calls[0];
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

  it("says when every row of a callout fell in one afternoon", () => {
    // Every outage session, and only those, hit an error.
    const outage = new Set(run.outage.ids);
    renderViz(AskPatterns, {
      records: run.records.map((record) => ({
        ...record,
        facets: { ...record.facets, error: outage.has(record.id) },
      })),
    });
    const item = screen.getByText(/when the run hit an error/).closest("li");
    expect(item).toHaveTextContent(
      new RegExp(`elsewhere, all ${OUTAGE_COUNT} on \\w+ \\d+, 2–5 PM`),
    );
    expect(
      screen.getByText(/comparisons: \d+ groups checked/),
    ).toBeInTheDocument();
  });

  it("switches the answer it looks at", () => {
    const { onFocus } = renderViz(AskPatterns);
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect(onFocus).toHaveBeenCalledWith({ configId: RESOLVED, answer: "Yes" });
  });
});

describe("AskHeatmap", () => {
  it("shows Day × Hour and selects a cell's rows", () => {
    const { onSelect } = renderViz(AskHeatmap);
    const grid = screen.getByRole("grid");
    const cells = within(grid).getAllByRole("gridcell");
    expect(cells).toHaveLength(7 * 24);
    const outage = cells.find(
      (cell) =>
        /2 PM–3 PM: \d+ of \d+ “No” \(100%\) vs \d+% overall/.test(
          cell.getAttribute("aria-label"),
        ) &&
        cell.getAttribute("aria-label").includes(
          new Date(run.outage.day).toLocaleDateString(undefined, {
            day: "numeric",
          }),
        ),
    );
    expect(outage).toBeTruthy();
    fireEvent.click(outage);
    const [ids, label] = onSelect.mock.calls[0];
    ids.forEach((id) => expect(run.outage.ids).toContain(id));
    expect(label).toMatch(/2 PM–3 PM$/);
  });

  it("totals each hour along the bottom and counts rows in the cells", () => {
    renderViz(AskHeatmap);
    const grid = screen.getByRole("grid");
    const totals = within(grid)
      .getAllByLabelText(/^\d+ [AP]M–\d+ [AP]M: /)
      .filter((el) => el.getAttribute("role") !== "gridcell");
    expect(totals).toHaveLength(24);
    const answered = totals.reduce((sum, el) => {
      const found = el.getAttribute("aria-label").match(/: \d+ of (\d+) “No”/);
      return sum + (found ? Number(found[1]) : 0);
    }, 0);
    expect(answered).toBe(run.records.length);
    expect(within(grid).getByText("All days, %")).toBeInTheDocument();
  });

  it("dots the cells with rows that hit an error", () => {
    renderViz(AskHeatmap);
    const cells = within(screen.getByRole("grid")).getAllByRole("gridcell");
    const total = cells.reduce((sum, cell) => {
      const found = cell
        .getAttribute("aria-label")
        .match(/ · (\d+) hit an error$/);
      return sum + (found ? Number(found[1]) : 0);
    }, 0);
    expect(total).toBe(OUTAGE_ERRORS);
    expect(screen.getByText("Run hit an error")).toBeInTheDocument();
  });

  it("pivots to users by day and to one question against another", () => {
    renderViz(AskHeatmap);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Rows/ }));
    fireEvent.click(screen.getByRole("option", { name: "User × Day" }));
    expect(
      screen.getByText(/The 15 busiest users; \d+ more are left out\./),
    ).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Rows/ }));
    fireEvent.click(
      screen.getByRole("option", { name: "Question × Question" }),
    );
    const cells = within(screen.getByRole("grid")).getAllByRole("gridcell");
    expect(cells).toHaveLength(2 * QUESTIONS[1].labels.length);
    expect(cells[0].getAttribute("aria-label")).toMatch(
      /^Of \d+ that answered “Yes”/,
    );
  });
});
