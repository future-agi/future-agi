import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import CustomTraceRenderer from "../CustomTraceRenderer";
import TokenCell from "../TokenCell";

// Keep both production renderers, the formatter and MUI tooltips real. Only
// navigation and decorative icons are stubbed; icons must not fetch assets.
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => React.createElement("span", { "data-testid": icon }),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../useNavigationHandlers", () => ({
  useNavigationHandlers: () => ({
    handleTraceClick: vi.fn(),
    handleSpanClick: vi.fn(),
  }),
}));

const usage = { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 };
const coverage = {
  scope: "observed_requests",
  source_summary_present: true,
  request_count: 3,
  coverage_unknown: true,
};
const explanation =
  "Counts only the 3 model calls we received. The agent's own run summary isn't added, so the real total may be higher.";
const componentColumns = [
  ["prompt_tokens", 100],
  ["completion_tokens", 20],
];

const renderColumn = (colId, overrides = {}) =>
  render(
    React.createElement(CustomTraceRenderer, {
      colDef: { context: { sourceColumn: { id: colId } } },
      context: { entityType: "trace" },
      value: usage[colId],
      data: { ...usage },
      ...overrides,
    }),
  );

const expectScalar = (container, value) => {
  expect(container.textContent).toBe(value.toLocaleString());
  expect(
    screen.queryByTestId("solar:arrow-down-linear"),
  ).not.toBeInTheDocument();
  expect(screen.queryByTestId("solar:arrow-up-linear")).not.toBeInTheDocument();
  expect(
    screen.queryByTestId("solar:info-circle-linear"),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Usage breakdown")).not.toBeInTheDocument();
};

describe("CustomTraceRenderer token columns", () => {
  it.each(componentColumns)(
    "renders %s as a scalar through grid dispatch",
    (colId, count) => {
      const { container } = renderColumn(colId);
      expectScalar(container, count);
    },
  );

  it.each(componentColumns)(
    "renders known zero for %s without borrowing the sibling count",
    (colId) => {
      const data = { ...usage, [colId]: 0 };
      data.total_tokens = data.prompt_tokens + data.completion_tokens;
      const { container } = renderColumn(colId, { value: 0, data });
      expectScalar(container, 0);
    },
  );

  it.each(componentColumns)(
    "falls back to the row component for %s when the cell value is absent",
    (colId, count) => {
      const { container } = renderColumn(colId, { value: undefined });
      expectScalar(container, count);
    },
  );

  it.each([120, 100])(
    "uses row total in the breakdown and tooltip when total cell value is %s",
    async (value) => {
      renderColumn("total_tokens", { value });
      expect(screen.getByText("(Σ 120)")).toBeInTheDocument();
      expect(screen.getByTestId("solar:arrow-down-linear")).toBeInTheDocument();
      expect(screen.getByTestId("solar:arrow-up-linear")).toBeInTheDocument();

      fireEvent.mouseOver(screen.getByTestId("solar:info-circle-linear"));
      const tooltip = await screen.findByRole("tooltip");
      expect(within(tooltip).getByText("Usage breakdown")).toBeInTheDocument();
      for (const [label, count] of [
        ["Input usage", "100"],
        ["Output usage", "20"],
        ["Total usage", "120"],
      ]) {
        const row =
          within(tooltip).getByText(label).parentElement.parentElement;
        expect(within(row).getByText(count)).toBeInTheDocument();
      }
    },
  );

  it.each(componentColumns)(
    "qualifies the row component for %s when coverage is unknown and value is absent",
    async (colId, count) => {
      const { container } = renderColumn(colId, {
        value: undefined,
        data: { ...usage, _captureCoverage: coverage },
      });
      const label = `${count} tokens*`;
      expect(container.textContent).toBe(label);
      expect(
        screen.queryByTestId("solar:info-circle-linear"),
      ).not.toBeInTheDocument();
      fireEvent.mouseOver(screen.getByText(label));
      expect(await screen.findByRole("tooltip")).toHaveTextContent(
        `${count} tokens. ${explanation}`,
      );
    },
  );

  it.each([...componentColumns, ["total_tokens", 120]])(
    "preserves coverage-qualified %s with its supplied count",
    async (colId, count) => {
      const { container } = renderColumn(colId, {
        data: { ...usage, _captureCoverage: coverage },
      });
      const label = `${count} tokens*`;
      expect(container.textContent).toBe(label);
      fireEvent.mouseOver(screen.getByText(label));
      expect(await screen.findByRole("tooltip")).toHaveTextContent(
        `${count} tokens. ${explanation}`,
      );
      expect(screen.queryByText("Usage breakdown")).not.toBeInTheDocument();
    },
  );

  it.each(["prompt_tokens", "completion_tokens", "total_tokens"])(
    "preserves coverage-qualified zero for %s",
    (colId) => {
      const { container } = renderColumn(colId, {
        value: 0,
        data: {
          prompt_tokens: 0,
          completion_tokens: 0,
          total_tokens: 0,
          _captureCoverage: { ...coverage, request_count: 0 },
        },
      });
      expect(container.textContent).toBe("0 tokens*");
      expect(
        screen.queryByTestId("solar:info-circle-linear"),
      ).not.toBeInTheDocument();
    },
  );

  it("keeps legacy direct total callers, scalar totals and empty totals", () => {
    const { container, rerender } = render(
      React.createElement(TokenCell, { value: 120, data: { ...usage } }),
    );
    expect(screen.getByText("(Σ 120)")).toBeInTheDocument();

    rerender(React.createElement(TokenCell, { value: 1234, data: {} }));
    expectScalar(container, 1234);

    rerender(React.createElement(TokenCell, { value: 0, data: {} }));
    expect(container.textContent).toBe("-");
  });
});
