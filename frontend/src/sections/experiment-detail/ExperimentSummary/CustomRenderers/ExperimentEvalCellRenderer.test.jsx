import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import ExperimentEvalCellRenderer from "./ExperimentEvalCellRenderer";

// Exercise the production renderer, normalizer and NumericCell; no component mocks.
afterEach(cleanup);

const mountCell = (value, outputType = "score", reverseOutput = false) => {
  const eGridCell = { style: { backgroundColor: "old-tint" } };
  render(
    <ExperimentEvalCellRenderer
      value={value}
      eGridCell={eGridCell}
      colDef={{ col: { output_type: outputType, reverse_output: reverseOutput } }}
    />,
  );
  return eGridCell;
};

describe.each(["score", "choices", "Pass/Fail"])(
  "stats percentages for %s",
  (outputType) => {
    it.each([
      [0, "0%"],
      [0.5, "0.5%"],
      [1, "1%"],
      [2, "2%"],
      [50, "50%"],
      [100, "100%"],
    ])("renders API average %s as %s without scaling again", (average, text) => {
      // calculate_column_average already converts the 0-1 mean to 0-100.
      const cell = mountCell(average, outputType);
      expect(screen.getByText(text)).toBeInTheDocument();
      expect(cell.style.backgroundColor).toBe(
        interpolateColorBasedOnScore(average, 100, false),
      );
    });
  },
);

it.each([0.5, 1, 90])("inverts the actual %s percent tint only once", (average) => {
  const cell = mountCell(average, "score", true);
  expect(screen.getByText(`${average}%`)).toBeInTheDocument();
  expect(cell.style.backgroundColor).toBe(
    interpolateColorBasedOnScore(average, 100, true),
  );
});

it("keeps fractional percentage precision returned by stats", () => {
  mountCell(0.01);
  expect(screen.getByText("0.01%")).toBeInTheDocument();
});

it.each([
  [0.5, "0.5000"],
  [1, "1"],
  [250, "250"],
])("leaves numeric output %s unscaled and untinted", (value, text) => {
  const cell = mountCell(value, "numeric");
  expect(screen.getByText(text)).toBeInTheDocument();
  expect(cell.style.backgroundColor).toBe("");
  expect(screen.queryByText(/%/)).toBeNull();
});

it("retains normalized scores and labels in raw choice envelopes", () => {
  const cell = mountCell({ score: 0.25, choices: ["Bad", "Good"] }, "choices");
  expect(screen.getByText("Bad, Good")).toBeInTheDocument();
  expect(cell.style.backgroundColor).toBe(
    interpolateColorBasedOnScore(25, 100, false),
  );
});

it("retains normalized scores in raw score envelopes", () => {
  const cell = mountCell({ score: 0.25 });
  expect(screen.getByText("25%")).toBeInTheDocument();
  expect(cell.style.backgroundColor).toBe(
    interpolateColorBasedOnScore(25, 100, false),
  );
});

it.each(["Passed", "Failed"])("preserves the raw pass/fail label %s", (value) => {
  mountCell(value, "Pass/Fail");
  expect(screen.getByText(value)).toBeInTheDocument();
});

it("keeps missing averages empty", () => {
  mountCell(null);
  expect(screen.queryByText(/%/)).toBeNull();
});
