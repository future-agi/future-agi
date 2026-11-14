import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme, hexToRgb } from "@mui/material/styles";
import userEvent from "@testing-library/user-event";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { defaultProps } from "src/theme/overrides/default-props";
import { tabs } from "src/theme/overrides/components/tabs";
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
  it("exposes full cell values regardless of length while distinguishing null and empty strings", () => {
    const values = ["W".repeat(36), "x".repeat(80), "short", 0, 123.5, false];
    renderResult({
      result: {
        ...result,
        columns: [{ name: "value", type: "String" }],
        rows: [...values, null, ""].map((value) => [value]),
      },
    });
    for (const value of values) {
      expect(
        screen.getByRole("cell", { name: String(value), exact: true }),
      ).toHaveAttribute("title", String(value));
    }
    expect(screen.getByRole("cell", { name: "NULL" })).not.toHaveAttribute(
      "title",
    );
    expect(screen.getByLabelText("Empty string")).toHaveTextContent("''");
    expect(screen.getByLabelText("Empty string")).toHaveAttribute(
      "title",
      "Empty string",
    );
    expect(
      screen.getByLabelText("Empty string").closest("td"),
    ).not.toHaveAttribute("title");
  });
  it.each(["light", "dark"])(
    "shows keyboard focus on both result tabs with production %s defaults",
    async (mode) => {
      const theme = createTheme({ palette: palette(mode), typography });
      const defaults = defaultProps(theme);
      const overrides = tabs(theme);
      theme.components = {
        MuiTab: { ...defaults.MuiTab, ...overrides.MuiTab },
        MuiTabs: { ...defaults.MuiTabs, ...overrides.MuiTabs },
      };
      expect(theme.components.MuiTab.defaultProps.disableRipple).toBe(true);
      const user = userEvent.setup();
      render(
        <ThemeProvider theme={theme}>
          <QueryResults result={result} />
        </ThemeProvider>,
      );
      const table = screen.getByRole("tab", { name: "Table", exact: true });
      const chart = screen.getByRole("tab", { name: "Chart", exact: true });
      const assertFocus = (tab) => {
        expect(tab).toHaveFocus();
        expect(tab).toHaveClass("Mui-focusVisible");
        expect(tab.querySelector(".MuiTouchRipple-root")).toBeNull();
        const style = getComputedStyle(tab);
        expect(style.outline).toBe("2px solid");
        expect(style.outlineColor).toBe(hexToRgb(theme.palette.text.primary));
        expect(style.outlineOffset).toBe("-3px");
      };
      await user.tab();
      assertFocus(table);
      await user.keyboard("{ArrowRight}");
      assertFocus(chart);
      expect(chart).toHaveAttribute("aria-selected", "false");
      expect(table).toHaveAttribute("aria-selected", "true");
      expect(table).not.toHaveClass("Mui-focusVisible");
      expect(getComputedStyle(table).outline).not.toBe("2px solid");
      await user.keyboard("{Enter}");
      expect(chart).toHaveAttribute("aria-selected", "true");
      await user.keyboard("{ArrowLeft}{Enter}");
      assertFocus(table);
      expect(
        screen.getByRole("table", { name: "SQL query results" }),
      ).toBeVisible();
    },
  );
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
