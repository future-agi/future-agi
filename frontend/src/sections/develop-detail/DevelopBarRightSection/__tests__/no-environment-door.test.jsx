// The dataset toolbar has no Environment door and mounts no environment
// drawer, even for a dataset whose kind reads task_suite. Every gate is open
// here (launch off), so the absent door cannot come from a hidden surface.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { h, Null } = vi.hoisted(() => ({
  h: { actions: null },
  Null: () => null,
}));

vi.mock("src/sections/common/ux/oneview/ActionBar", () => ({
  default: ({ actions }) => {
    h.actions = actions;
    return null;
  },
}));
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({ default: Null }));
vi.mock("../../DataTab/DataGrid/useDataToolbarView", () => ({
  default: () => ({
    gridView: { density: "comfortable", setDensity: () => {} },
    viewState: null,
    applyView: () => {},
    modified: false,
    resetView: () => {},
  }),
}));
vi.mock("../../DataTab/DataGrid/useDataGridLayout", () => ({
  default: () => ({ isDefault: true, hiddenCount: 0, openMenu: () => {} }),
  DATA_GRID_SURFACE: "data-grid",
}));
vi.mock("../../DataTab/DataGrid/FloatingSelectionBar", () => ({
  default: Null,
}));
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: () => {} }),
}));
vi.mock("../../DataTab/DatapointDrawerV2/useRowViewUrl", () => ({
  default: () => ({ openRow: () => {} }),
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: () => {} }));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  DatasetExportDialog: Null,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { edit: true, download: true, run: true },
    reason: () => "",
  }),
}));
vi.mock("src/sections/develop/AddRowDrawer/AddRowDrawer", () => ({
  default: Null,
}));
vi.mock("../../AddColumn/AddColumnDrawer", () => ({ default: Null }));
vi.mock("../DevelopDataSelectionActive", () => ({ default: Null }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: () => {},
}));
vi.mock(
  "src/sections/develop/AddRowDrawer/EditSyntheticData/SyntheticSummaryDrawer",
  () => ({ default: Null }),
);
vi.mock("src/sections/develop/AddRowDrawer/EditSyntheticData/state", () => ({
  useEditSyntheticDataStore: () => ({ setOpenSummaryDrawer: () => {} }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", user: {} }),
}));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "task_suite", dataset: null }),
}));
vi.mock("src/sections/common/Values/SchemaDrawer", () => ({
  default: Null,
}));
vi.mock("src/sections/develop-detail/TaskSuite/EnvironmentDrawer", () => ({
  default: ({ open }) => (
    <div data-testid="environment-drawer" data-open={String(Boolean(open))} />
  ),
}));

import DevelopDataRightSection from "../DevelopDataRightSection";

const renderToolbar = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/develop/ds-1"]}>
        <Routes>
          <Route
            path="/develop/:dataset"
            element={<DevelopDataRightSection />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};
const labels = () => (h.actions ?? []).map((a) => a.label);

beforeEach(() => {
  setLaunchOverrideForTest({ launch: false });
  h.actions = null;
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("dataset toolbar", () => {
  it("has no Environment door next to the read-only Schema door", () => {
    renderToolbar();
    expect(labels()).toEqual(expect.arrayContaining(["Add column", "Schema"]));
    expect(labels()).not.toContain("Environment");
    expect((h.actions ?? []).map((a) => a.id)).not.toContain("environment");
  });

  it("mounts no environment drawer", () => {
    renderToolbar();
    expect(h.actions).not.toBeNull();
    expect(screen.queryByTestId("environment-drawer")).toBeNull();
  });
});
