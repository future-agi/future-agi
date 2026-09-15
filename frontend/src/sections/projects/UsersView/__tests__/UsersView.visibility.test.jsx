import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";

const fixture = vi.hoisted(() => {
  const noop = () => {};
  return {
    gridProps: null,
    filters: [],
    header: {
      setHeaderConfig: noop,
      setActiveViewConfig: noop,
      registerGetViewConfig: noop,
    },
    store: {
      columns: [],
      searchQuery: "",
      sortParams: [],
      gridApi: null,
      clearSelection: noop,
      resetStore: noop,
      setColumns: noop,
      updateColumnVisibility: noop,
      addCustomColumns: noop,
      removeCustomColumns: noop,
      openCustomColumnDialog: false,
      setOpenCustomColumnDialog: noop,
    },
  };
});
vi.mock("../Store/usersStore", () => {
  const store = (selector) => selector(fixture.store);
  store.setState = () => {};
  store.getState = () => fixture.store;
  return { default: store };
});
vi.mock("../UsersGrid", () => ({
  default: (props) => {
    fixture.gridProps = props;
    return <div role="grid" aria-label="Users results" />;
  },
}));
vi.mock("../UsersEmptyScreen", () => ({
  default: () => <div>Confirmed empty users</div>,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "test-workspace" }),
}));
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => fixture.header,
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return { useUrlState: (_key, initial) => useState(initial) };
});
vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
}));
vi.mock("../../LLMTracing/useLLMTracingFilters", () => ({
  useLLMTracingFilters: (_filters, dateFilter) => ({
    validatedFilters: fixture.filters,
    filters: fixture.filters,
    setFilters: vi.fn(),
    dateFilter,
    setDateFilter: vi.fn(),
  }),
}));
vi.mock("../../LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("../../LLMTracing/ObserveToolbar", () => ({ default: () => null }));
vi.mock("../../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../../LLMTracing/CustomColumnDialog", () => ({ default: () => null }));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);

import UsersView from "../UsersView";

describe("Users view content visibility", () => {
  it("keeps the grid visible when an initial read settles without a result", () => {
    render(
      <HelmetProvider>
        <UsersView />
      </HelmetProvider>,
    );
    expect(screen.getByRole("grid", { name: "Users results" })).toBeVisible();
    act(() => fixture.gridProps.setIsLoading(false));
    expect(screen.getByRole("grid", { name: "Users results" })).toBeVisible();
    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
  });

  it("shows the empty screen only after confirmed exhaustion, then restores the grid for another read", () => {
    render(
      <HelmetProvider>
        <UsersView />
      </HelmetProvider>,
    );
    act(() => {
      fixture.gridProps.setHasData(false);
      fixture.gridProps.setSearchState("empty");
      fixture.gridProps.setIsLoading(false);
    });
    expect(screen.getByText("Confirmed empty users")).toBeVisible();
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
    act(() => fixture.gridProps.setIsLoading(true));
    expect(screen.getByRole("grid", { name: "Users results" })).toBeVisible();
    expect(screen.queryByText("Confirmed empty users")).not.toBeInTheDocument();
  });
});
