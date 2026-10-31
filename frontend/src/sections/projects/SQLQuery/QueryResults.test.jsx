import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import QueryResults, { numericColumns } from "./QueryResults";

const renderResult = (props) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <QueryResults {...props} />
    </ThemeProvider>,
  );
const result = {
  columns: [
    { name: "locale", type: "String" },
    { name: "score", type: "Float64" },
  ],
  rows: [
    ["en", 0.5],
    ["fr", null],
  ],
  elapsed_ms: 15,
  truncated: false,
};

describe("SQL query results", () => {
  it("renders exact values and nulls in the accessible table", () => {
    renderResult({ result });
    expect(
      screen.getByRole("table", { name: "SQL query results" }),
    ).toBeInTheDocument();
    expect(screen.getByText("0.5")).toBeInTheDocument();
    expect(screen.getByText("NULL")).toBeInTheDocument();
    expect(screen.getByText("2 rows · 2 columns · 15 ms")).toBeVisible();
  });
  it("renders malicious labels as text, not markup", () => {
    renderResult({
      result: { ...result, rows: [["<img src=x onerror=alert(1)>", 2]] },
    });
    expect(
      screen.getByText("<img src=x onerror=alert(1)>"),
    ).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
  it("states truncation and download parity", () => {
    renderResult({ result: { ...result, truncated: true } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Downloads contain these same rows",
    );
  });
  it("does not treat numeric-looking strings or nonfinite values as chart measures", () => {
    expect(
      numericColumns(result.columns, [
        ["10", 0.5],
        ["20", null],
      ]),
    ).toEqual([result.columns[1]]);
    expect(numericColumns(result.columns, [["10", Infinity]])).toEqual([]);
  });
  it("shows an actionable empty state", () => {
    renderResult({ result: { ...result, rows: [] } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "No rows in this window",
    );
  });
  it("can switch from chart configuration back to exact table data", () => {
    renderResult({ result, chart: { type: "bar", x: "locale", y: "score" } });
    fireEvent.click(screen.getByRole("tab", { name: "Table", exact: true }));
    expect(
      screen.getByRole("table", { name: "SQL query results" }),
    ).toBeInTheDocument();
  });
});

it.each([
  undefined,
  { columns: [{ name: "name", type: "String" }], rows: [], elapsed_ms: 1 },
  {
    columns: [{ name: "name", type: "String" }],
    rows: [["text only"]],
    elapsed_ms: 1,
  },
])("emits a valid table config without empty axes", (data) => {
  const onChartChange = vi.fn();
  renderResult({ result: data, onChartChange });
  expect(onChartChange).toHaveBeenLastCalledWith({ type: "table" });
});
