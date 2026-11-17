// The Data grid's `{ } Expression` filter on this server: the table read
// does not apply an expression. The button stays on screen, turned off and
// described by the shared line, and opens nothing. The grid keeps no
// expression: a link's expr= (or a filters= expression) is not read, and
// setting one keeps none, so no pill or active state says the rows are
// filtered and nothing is sent. The prototype keeps the filter as it is.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import {
  getDataGridFilters,
  getDataGridView,
  useDataGridUrlSync,
  useDataGridView,
  useDataGridViewStore,
} from "src/sections/develop-detail/DataTab/DataGrid/useDataGridView";
import ExpressionFilterButton from "../ExpressionFilterButton";

const h = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: h.request, post: h.request },
  };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));
/* eslint-disable react/prop-types */
vi.mock("../ExpressionFilterDialog", () => ({
  default: ({ open }) =>
    open ? <div role="dialog" aria-label="Filter by expression" /> : null,
}));
/* eslint-enable react/prop-types */

const LINE = "Not available in this release";
const EXPR = "len(question) > 3";
const hasExpression = (datasetId) =>
  getDataGridFilters(datasetId).some(
    (f) => f.column_id === "system:expression",
  );

// The grid's URL sync writes back against window.location, as the app's
// BrowserRouter keeps it. So the link is opened in the window's own history,
// under a BrowserRouter, with one split list for every render as the grid
// passes. A render loop fails the case instead of hanging the file.
const NO_SPLITS = [];
const MAX_RENDERS = 50;
const hydrateFrom = (datasetId, search) => {
  window.history.replaceState(
    {},
    "",
    `/dashboard/develop/${datasetId}${search}`,
  );
  let renders = 0;
  return renderHook(
    () => {
      renders += 1;
      if (renders > MAX_RENDERS)
        throw new Error(
          `The grid's URL sync rendered more than ${MAX_RENDERS} times`,
        );
      return useDataGridUrlSync(datasetId, NO_SPLITS);
    },
    {
      wrapper: ({ children }) => <BrowserRouter>{children}</BrowserRouter>,
    },
  );
};

let client;
beforeEach(() => {
  h.request.mockReset().mockImplementation(() => {
    throw new Error("Network is forbidden in this test");
  });
  useDataGridViewStore.setState({ views: {} });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  setRealBackendForTests(null);
  window.history.replaceState({}, "", "/");
});

describe("the grid's Expression filter on this server", () => {
  it("stays on screen, turned off and described by the line, and opens nothing", () => {
    setRealBackendForTests(true);
    render(
      <QueryClientProvider client={client}>
        <ExpressionFilterButton datasetId="ds-expr-button" />
      </QueryClientProvider>,
    );

    const button = screen.getByRole("button", { name: "Expression filter" });
    expect(button).toBeVisible();
    expect(button).toHaveTextContent("Expression");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAccessibleDescription(LINE);

    fireEvent.click(button);
    expect(
      screen.queryByRole("dialog", { name: "Filter by expression" }),
    ).toBeNull();
    expect(h.request).not.toHaveBeenCalled();
  });

  it("does not read a link's expr= as an applied filter", () => {
    setRealBackendForTests(true);
    hydrateFrom("ds-expr-link", `?tab=data&expr=${encodeURIComponent(EXPR)}`);

    expect(getDataGridView("ds-expr-link").expression).toBeNull();
    expect(hasExpression("ds-expr-link")).toBe(false);
  });

  it("does not read a filters= link's expression as applied", () => {
    setRealBackendForTests(true);
    const list = [
      {
        column_id: "system:expression",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: EXPR,
        },
      },
    ];
    hydrateFrom(
      "ds-expr-drill",
      `?tab=data&filters=${encodeURIComponent(JSON.stringify(list))}`,
    );

    expect(getDataGridView("ds-expr-drill").expression).toBeNull();
    expect(hasExpression("ds-expr-drill")).toBe(false);
  });

  it("keeps no expression when one is set (a saved view, the dialog)", () => {
    setRealBackendForTests(true);
    const { result } = renderHook(() => useDataGridView("ds-expr-set"));

    act(() => result.current.setExpression(EXPR));

    expect(getDataGridView("ds-expr-set").expression).toBeNull();
    expect(hasExpression("ds-expr-set")).toBe(false);
  });
});

// Guards (green before and after): the prototype applies expressions.
describe("the grid's Expression filter in the prototype", () => {
  it("reads a link's expr= and filters by it", () => {
    setRealBackendForTests(false);
    hydrateFrom("ds-expr-proto", `?tab=data&expr=${encodeURIComponent(EXPR)}`);

    expect(getDataGridView("ds-expr-proto").expression).toBe(EXPR);
    expect(hasExpression("ds-expr-proto")).toBe(true);
  });

  it("keeps the button working", () => {
    setRealBackendForTests(false);
    render(
      <QueryClientProvider client={client}>
        <ExpressionFilterButton datasetId="ds-expr-proto-button" />
      </QueryClientProvider>,
    );

    const button = screen.getByRole("button", { name: "Expression filter" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(
      screen.getByRole("dialog", { name: "Filter by expression" }),
    ).toBeInTheDocument();
  });
});
