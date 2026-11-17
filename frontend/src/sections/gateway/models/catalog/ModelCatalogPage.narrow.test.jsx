// Below the lg breakpoint (1024 px, beside the facet panel) the catalog's
// columns run past the grid's edge. The model name stays pinned in view and
// the edge the columns run past is shaded. On a phone nothing is pinned.
import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, waitFor } from "@testing-library/react";

import { endpoints } from "src/utils/axios";

import {
  answerFrom,
  contractFailures,
  demoResponses,
  renderModelsAt,
} from "../test-fixtures/harness";
import ModelCatalogPage from "./ModelCatalogPage";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "http://localhost:28490/v1" },
  }),
}));
// useResponsive("down", key): which breakpoints the window is below.
const below = vi.hoisted(() => ({ lg: false, md: false }));
vi.mock("src/hooks/use-responsive", () => ({
  useResponsive: (query, key) =>
    query === "down" ? Boolean(below[key]) : false,
}));
const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

const open = () => {
  api.get.mockImplementation(answerFrom(demoResponses(endpoints)));
  return renderModelsAt("/dashboard/gateway/models", <ModelCatalogPage />);
};
const name = () => grid.props.columnDefs.find((c) => c.colId === "name");
const rows = () =>
  waitFor(() => expect(grid.props?.rowData?.length).toBeGreaterThan(0));

afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
});
beforeEach(() => {
  api.get.mockReset();
  grid.props = null;
  below.lg = false;
  below.md = false;
  window.localStorage.clear();
});

describe("Models catalog at narrow widths", () => {
  it("pins the model name below lg and shades the edge", async () => {
    below.lg = true;
    open();
    await rows();
    expect(name().pinned).toBe("left");
    expect(typeof grid.props.onBodyScroll).toBe("function");
    // A layout saved at another width never decides the pin.
    const applyColumnState = vi.fn();
    act(() => grid.props.onGridReady({ api: { applyColumnState } }));
    expect(applyColumnState).toHaveBeenLastCalledWith({
      state: [{ colId: "name", pinned: "left" }],
    });
  });

  it("does not pin it at lg and wider", async () => {
    open();
    await rows();
    expect(name().pinned).toBeFalsy();
    const applyColumnState = vi.fn();
    act(() => grid.props.onGridReady({ api: { applyColumnState } }));
    expect(applyColumnState).toHaveBeenLastCalledWith({
      state: [{ colId: "name", pinned: null }],
    });
  });

  it("does not pin it on a phone", async () => {
    below.lg = true;
    below.md = true;
    open();
    await rows();
    expect(name().pinned).toBeFalsy();
  });
});
