import React from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { AskHeatmap } from "../index";
import { QUESTIONS, RESOLVED, syntheticRun } from "./fixtures/syntheticRun";

vi.mock("src/components/iconify", () => ({ default: () => null }));

// The charts stop a window at "now"; pin it to the synthetic run's evening.
beforeAll(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 15, 18, 30));
});
afterAll(() => vi.useRealTimers());

const theme = createTheme({ palette: palette("light"), typography });
const run = syntheticRun();

// Five answers five days apart over a month: no Day × Hour cell reaches 3.
const sparseRecords = () =>
  run.records.slice(0, 5).map((record, i) => ({
    ...record,
    time: new Date(2026, 8, 20 + i * 5, 10).toISOString(),
  }));
const sparseWindow = [
  new Date(2026, 8, 15).toISOString(),
  new Date(2026, 9, 16).toISOString(),
];

function renderHeatmap(props = {}) {
  render(
    <ThemeProvider theme={theme}>
      <AskHeatmap
        records={sparseRecords()}
        questions={QUESTIONS}
        focus={{ configId: RESOLVED, answer: null }}
        window={sparseWindow}
        running={false}
        onFocus={vi.fn()}
        onSelect={vi.fn()}
        {...props}
      />
    </ThemeProvider>,
  );
  return screen.getByRole("region", { name: "Heatmap of answers" });
}

describe("W10-INSTANT-EVAL-U01 a sparse Day × Hour heatmap", () => {
  it("points at the Rows switcher, and reads more where the run can", () => {
    const onReadMore = vi.fn();
    const card = renderHeatmap({ onReadMore });
    expect(
      within(card).getByText("Not enough answers for time patterns"),
    ).toBeInTheDocument();
    expect(
      within(card).getByRole("combobox", { name: "Rows" }),
    ).toBeInTheDocument();
    // Nothing to colour without a grid.
    expect(within(card).queryByRole("combobox", { name: "Colour" })).toBeNull();
    expect(
      within(card).getByText(
        /Switch Rows to Users, Models or Turns, or read more rows\./,
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Read more" }));
    expect(onReadMore).toHaveBeenCalledTimes(1);
  });

  it("shows no dead Read more on a run that can't read more, and Models shows its answers", () => {
    const records = sparseRecords();
    const card = renderHeatmap({ records });
    expect(
      within(card).getByText("Not enough answers for time patterns"),
    ).toBeInTheDocument();
    // No more rows to read: no Read more at all, rather than a disabled one.
    expect(
      within(card).queryByRole("button", { name: "Read more" }),
    ).toBeNull();
    expect(within(card).queryByText(/answer split/)).toBeNull();
    expect(
      within(card).getByText(
        /Switch Rows to Users, Models or Turns, or start a new question with a larger scope\./,
      ),
    ).toBeInTheDocument();

    fireEvent.mouseDown(within(card).getByRole("combobox", { name: "Rows" }));
    fireEvent.click(screen.getByRole("option", { name: "Models" }));
    expect(
      screen.queryByText("Not enough answers for time patterns"),
    ).toBeNull();
    const cells = within(
      screen.getByRole("grid", { name: "Answers by group" }),
    ).getAllByRole("gridcell");
    expect(
      cells.some((cell) =>
        cell
          .getAttribute("aria-label")
          .startsWith(`${records[0].facets.model}:`),
      ),
    ).toBe(true);
  });
});

describe("W10-INSTANT-EVAL-U02 the sparse heatmap's Read more", () => {
  it("says what it costs where answers are billed", () => {
    const onReadMore = vi.fn();
    const card = renderHeatmap({
      onReadMore,
      readMoreCost: "about 60 credits",
    });
    fireEvent.click(
      within(card).getByRole("button", {
        name: "Read more · about 60 credits",
      }),
    );
    expect(onReadMore).toHaveBeenCalledTimes(1);
  });
});
