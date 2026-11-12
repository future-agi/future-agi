import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";

import { endpoints } from "src/utils/axios";

import {
  answerFrom,
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
vi.mock("src/hooks/use-responsive", () => ({ useResponsive: () => false }));

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

// The catalog's centre columns at their 1024 px minimum, in a 568 px grid.
const narrowApi = (left) => ({
  getHorizontalPixelRange: () => ({ left, right: left + 568 }),
  getDisplayedCenterColumns: () => [{ getActualWidth: () => 800 }],
  getColumnState: () => [],
});

beforeEach(() => {
  api.get.mockReset();
  api.get.mockImplementation(answerFrom(demoResponses(endpoints)));
  grid.props = null;
  window.localStorage.clear();
});

// At 1024 px the price columns lie past the grid's right
// edge; the grid shades that edge until the user has scrolled to the end.
describe("Models catalog sideways-scroll cue (layout)", () => {
  it("shades the grid's right edge while columns are off screen", async () => {
    renderModelsAt("/dashboard/gateway/models", <ModelCatalogPage />);
    await waitFor(() => expect(grid.props?.rowData?.length).toBeGreaterThan(0));
    act(() => grid.props.onGridSizeChanged({ api: narrowApi(0) }));
    expect(screen.getByTestId("gateway-grid-more-right")).toBeInTheDocument();
    act(() => grid.props.onBodyScroll({ api: narrowApi(232) }));
    expect(screen.queryByTestId("gateway-grid-more-right")).toBeNull();
  });
});
