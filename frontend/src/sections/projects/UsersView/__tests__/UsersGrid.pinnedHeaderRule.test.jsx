/* eslint-disable react/prop-types */
// C3: on the Users tab the header's bottom rule stopped where the pinned
// Actions column starts, so the Actions header had no underline while the
// rows under it did. clean-data-table.css draws that rule on the scrolling
// header viewport only; the grid now draws the same rule under its pinned
// headers. Layout is not measured here: every Box stamps the sx it was given
// on its element (data-sx), and the test reads the grid box's sx.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const { navigateMock, readMock, storeState } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  readMock: vi.fn(),
  storeState: {
    setGridApi: vi.fn(),
    searchQuery: "",
    selectedAll: false,
    selectedRowsData: [],
    setSelectedAll: vi.fn(),
    setSelectedRowsData: vi.fn(),
    clearSelection: vi.fn(),
    columns: [],
    setColumns: vi.fn(),
    filters: [],
    setFilters: vi.fn(),
  },
}));

vi.mock("@mui/material", async (importOriginal) => {
  const actual = await importOriginal();
  const { createElement, forwardRef } = await import("react");
  const Box = forwardRef(function SxStampedBox(props, ref) {
    return createElement(actual.Box, {
      ...props,
      ref,
      "data-sx": JSON.stringify(props.sx ?? null),
    });
  });
  return { ...actual, Box };
});
vi.mock("ag-grid-react", async (importOriginal) => {
  const { createElement, forwardRef } = await import("react");
  return {
    ...(await importOriginal()),
    AgGridReact: forwardRef(function AgGridReactDouble(_props, _ref) {
      return createElement("div", { "data-testid": "ag-grid" });
    }),
  };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("../Store/usersStore", async (importOriginal) => {
  const useUsersStore = () => storeState;
  useUsersStore.setState = vi.fn();
  return { ...(await importOriginal()), default: useUsersStore };
});
vi.mock("src/hooks/use-ag-theme", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgThemeWith: () => ({}),
}));
vi.mock("src/hooks/use-debounce", async (importOriginal) => ({
  ...(await importOriginal()),
  useDebounce: (value) => value,
}));
vi.mock("../common", async (importOriginal) => ({
  ...(await importOriginal()),
  getUsersColumnConfig: () => [
    { field: "user_id", headerName: "User ID" },
    { field: "num_traces", headerName: "No. of Traces" },
    { field: "actions", headerName: "Actions", pinned: "right" },
  ],
  buildUsersRequestFilters: (filters) => filters,
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigateMock,
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  readQuery: (...args) => readMock(...args),
}));

import UsersGrid from "../UsersGrid";

const sxOf = (element) =>
  JSON.parse(element?.getAttribute("data-sx") ?? "null");

const renderGrid = () =>
  render(
    <UsersGrid
      hasActiveFilter={false}
      setHasData={vi.fn()}
      setIsLoading={vi.fn()}
      setSearchState={vi.fn()}
      cellHeight="Short"
    />,
  );

// The box AG Grid renders into carries the grid's header and cell rules.
const gridRules = () => sxOf(screen.getByTestId("ag-grid").parentElement);

const ruleFor = (rules, className) =>
  Object.entries(rules ?? {}).find(([selector]) =>
    selector.split(",").some((part) => part.trim() === `& .${className}`),
  )?.[1];

describe("Users grid header rule under the pinned Actions column (C3)", () => {
  beforeEach(() => {
    storedValues.clear();
    readMock.mockReset().mockReturnValue(new Promise(() => {}));
  });

  it("draws the header's bottom rule under the pinned right header", () => {
    renderGrid();

    // Same rule clean-data-table.css draws under the scrolling header.
    expect(ruleFor(gridRules(), "ag-pinned-right-header")).toEqual({
      borderBottom: "1px solid var(--border-light)",
    });
  });

  it("draws it under a column pinned left as well", () => {
    renderGrid();

    expect(ruleFor(gridRules(), "ag-pinned-left-header")).toEqual({
      borderBottom: "1px solid var(--border-light)",
    });
  });

  it("keeps the grid's existing cell and overlay rules", () => {
    renderGrid();

    const rules = gridRules();
    expect(rules).toMatchObject({ height: "100%" });
    expect(rules).toHaveProperty(["& .ag-overlay-no-rows-wrapper a"], {
      pointerEvents: "auto",
    });
  });
});
