import { ThemeProvider, createTheme } from "@mui/material/styles";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ViewDetailsModal from "../ViewDetailsModal";

// Keep the real modal, numeric renderer and shared result formatters.
/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ErrorLocalizeCard", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/CompositeResultView", () => ({
  default: () => null,
}));
/* eslint-enable react/prop-types */

afterEach(cleanup);

const theme = createTheme({
  palette: {
    green: { 500: "#22c55e" },
    red: { 500: "#ef4444" },
  },
});

function renderScore(outputType, cellValue, status = "pass") {
  // The row endpoint returns output_type on column_config and the value/status
  // on the cell. The drawer merges them before opening ViewDetailsModal.
  const column = {
    id: "base-evaluation-column",
    output_type: outputType,
    group: { name: "Reference evaluation" },
    is_base_column: true,
  };
  const row = {
    row_id: "experiment-row",
    [column.id]: {
      cell_value: cellValue,
      status,
      metadata: {
        cell_metadata: { explanation: "Evaluation completed." },
      },
    },
  };

  render(
    <ThemeProvider theme={theme}>
      <ViewDetailsModal
        open
        onClose={vi.fn()}
        data={{ ...column, ...row[column.id] }}
      />
    </ThemeProvider>,
  );

  return screen.getByText("Score", { exact: true }).parentElement;
}

describe("W5-B-002 numeric evaluation detail", () => {
  it.each([
    ["42.5", "42.5000"],
    ["0.5", "0.5000"],
    [42.5, "42.5000"],
    [0.5, "0.5000"],
    [0, "0"],
    ["-2.75", "-2.7500"],
    ["1.234567", "1.2346"],
  ])("shows numeric %j without percentage conversion", (value, expected) => {
    const score = renderScore("numeric", value);

    // NumericCell uses four decimal places for non-integers, as in the drawer.
    expect(within(score).getByText(expected, { exact: true })).toBeInTheDocument();
    expect(score).not.toHaveTextContent("%");
  });

  it("preserves percentage formatting for normalized scores", () => {
    const score = renderScore("score", "0.425");

    expect(within(score).getByText("43%", { exact: true })).toBeInTheDocument();
  });

  it("preserves legacy scores without output metadata", () => {
    const score = renderScore(undefined, "0.5");

    expect(within(score).getByText("50%", { exact: true })).toBeInTheDocument();
  });

  it.each([
    ["choices", '["Relevant", "Clear"]'],
    ["score", '{"score":0.425,"choices":["Relevant","Clear"]}'],
  ])("preserves choice labels for %s output", (outputType, value) => {
    const score = renderScore(outputType, value);

    expect(within(score).getByText("Relevant", { exact: true })).toBeInTheDocument();
    expect(within(score).getByText("Clear", { exact: true })).toBeInTheDocument();
    expect(score).not.toHaveTextContent("%");
  });

  it.each(["Passed", "Failed", "pass", "fail"])(
    "preserves the %s pass/fail label",
    (value) => {
      const score = renderScore("pass/fail", value);

      expect(within(score).getByText(value, { exact: true })).toBeInTheDocument();
      expect(score).not.toHaveTextContent("%");
    },
  );

  it("keeps running numeric results in the loading state", () => {
    const score = renderScore("numeric", "42.5", "running");

    expect(score.querySelector(".MuiSkeleton-root")).not.toBeNull();
    expect(score).toHaveTextContent(/^Score$/);
  });

  it("keeps numeric evaluation errors visible", () => {
    const score = renderScore("numeric", "42.5", "error");

    expect(within(score).getByText("error", { exact: true })).toBeInTheDocument();
    expect(score).not.toHaveTextContent("42.5");
  });
});
