import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import AskHeatmap from "../AskHeatmap";
import { buildModelDayGrid, buildTimeGrid, gridPeak } from "../askPivot";
import { dayKeyOf } from "../askSummary";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light"), typography });

function longRun({ stride = 1, category = false } = {}) {
  const labels = category ? ["Success", "Failure"] : ["Yes", "No"];
  const question = {
    configId: "range-question",
    question: "Was the request successful?",
    answerType: category ? "category" : "yes_no",
    labels,
  };
  const days = Array.from({ length: 60 }, (_, i) =>
    dayKeyOf(new Date(2024, 0, 1 + i * stride)),
  );
  const entries = days.map((day, i) => ({
    n: 4,
    counts: { [question.configId]: { [labels[i < 29 ? 1 : 0]]: 4 } },
  }));
  const summary = {
    total: 240,
    questions: [question],
    counts: { [question.configId]: { [labels[0]]: 124, [labels[1]]: 116 } },
    window: {
      from: new Date(2024, 0, 1).toISOString(),
      to: new Date(2024, 0, 1 + 60 * stride).toISOString(),
    },
    groups: {
      day_hour: entries.map((entry, i) => ({
        ...entry,
        key: `${days[i]}|00`,
      })),
      model_day: entries.map((entry, i) => ({
        ...entry,
        key: `model-a|${days[i]}`,
      })),
    },
  };
  return { summary, question, days };
}

function drawableTotals(grid) {
  const totals = { n: 0, answered: 0, no: 0 };
  grid.rows.forEach((row) => {
    grid.columns.forEach((column) => {
      const cell = grid.cells.get(`${row.key}|${column.key}`);
      totals.n += cell?.n || 0;
      totals.answered += cell?.answered || 0;
      totals.no += cell?.byLabel.get("No") || 0;
    });
  });
  return totals;
}

// CellGrid draws through MUI Box, which sets width and height as CSS, never
// as svg attributes. jsdom measures no layout, so useWidth gives each
// container its 640px fallback: past 640 means wider than the container.
const drawnSize = (svg, side) => parseFloat(getComputedStyle(svg)[side]);

function renderRange(options) {
  const run = longRun(options);
  const onSelect = vi.fn();
  render(
    <ThemeProvider theme={theme}>
      <AskHeatmap
        summary={run.summary}
        questions={[run.question]}
        onSelect={onSelect}
      />
    </ThemeProvider>,
  );
  return { ...run, onSelect };
}

function selectModelDay() {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "Rows" }));
  fireEvent.click(screen.getByRole("option", { name: "Model × Day" }));
}

describe("W10-INSTANT-EVAL-L03: full heatmap day range", () => {
  it.each([
    ["Day × Hour", buildTimeGrid, "rows"],
    ["Model × Day", buildModelDayGrid, "columns"],
  ])("keeps the early failure cohort drawable in %s", (_name, build, axis) => {
    const { summary, question, days } = longRun();
    const grid = build({ summary, question });
    expect(grid[axis].map((day) => day.key)).toEqual(days);
    expect(drawableTotals(grid)).toEqual({ n: 240, answered: 240, no: 116 });
    expect(grid.overall.n).toBe(240);
    expect(grid.overall.byLabel.get("No")).toBe(116);
    expect(gridPeak(grid, "No")).toBe(1);
  });

  it("retains every populated day even without a window or with gaps", () => {
    const { summary, question, days } = longRun({ stride: 2 });
    delete summary.window;
    expect(
      buildTimeGrid({ summary, question }).rows.map((row) => row.key),
    ).toEqual(days);
    expect(
      buildModelDayGrid({ summary, question }).columns.map((col) => col.key),
    ).toEqual(days);
  });

  it("still pads a short window with empty days", () => {
    const { question } = longRun();
    const grid = buildTimeGrid({
      summary: {},
      question,
      window: [
        new Date(2024, 0, 1).toISOString(),
        new Date(2024, 0, 4).toISOString(),
      ],
    });
    expect(grid.rows.map((row) => row.key)).toEqual([
      "2024-01-01",
      "2024-01-02",
      "2024-01-03",
    ]);
  });

  it.each(["dayHour", "modelDay"])(
    "lets the reader inspect and select both ends of %s in a bounded viewport",
    (pivot) => {
      const { days, onSelect } = renderRange();
      if (pivot === "modelDay") selectModelDay();
      const viewport = screen.getByRole("region", {
        name: "Scrollable heatmap",
      });
      expect(viewport).toHaveStyle({ maxHeight: "480px", overflow: "auto" });
      expect(screen.getByText(/All 60 days with data/)).toBeInTheDocument();
      const svg = screen.getByRole("img", { name: /^Share of “No”/ });
      expect(
        drawnSize(svg, pivot === "modelDay" ? "width" : "height"),
      ).toBeGreaterThan(pivot === "modelDay" ? 640 : 480);
      fireEvent.keyDown(svg, { key: "ArrowRight" });
      expect(screen.getByRole("status")).toHaveTextContent(
        "4 of 4 “No” (100%)",
      );
      fireEvent.keyDown(svg, { key: "Enter" });
      const firstFilter = onSelect.mock.calls[0][0];
      if (pivot === "modelDay")
        expect(firstFilter).toEqual({ group: `model_day:model-a|${days[0]}` });
      else
        expect(firstFilter.time_from).toBe(new Date(2024, 0, 1).toISOString());
      for (let i = 1; i < days.length; i += 1)
        fireEvent.keyDown(svg, {
          key: pivot === "modelDay" ? "ArrowRight" : "ArrowDown",
        });
      expect(screen.getByRole("status")).toHaveTextContent("0 of 4 “No” (0%)");
      fireEvent.keyDown(svg, { key: "Enter" });
      const lastFilter = onSelect.mock.calls[1][0];
      if (pivot === "modelDay")
        expect(lastFilter).toEqual({ group: `model_day:model-a|${days[59]}` });
      else
        expect(lastFilter.time_from).toBe(new Date(2024, 0, 60).toISOString());
    },
  );

  it("keeps long Model × Day small multiples wide enough to inspect", () => {
    renderRange({ category: true });
    selectModelDay();
    expect(
      screen.getByRole("region", { name: "Scrollable heatmap" }),
    ).toHaveStyle({ maxHeight: "480px", overflow: "auto" });
    const panels = screen.getAllByRole("img", {
      name: /share of each model by day/,
    });
    expect(panels).toHaveLength(2);
    panels.forEach((panel) => {
      expect(drawnSize(panel, "width")).toBeGreaterThan(640);
    });
  });
});
