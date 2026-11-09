/* eslint-disable react/prop-types */
// MAIN-RULING-EXPORT-CONTRACT: only advanced formats require dataset.export.
// Keep the actual list/grid callers, menus, permission gates, dialog, row
// adapter and file writers. Stub unrelated data hooks and layout infrastructure.
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { render } from "src/utils/test-utils";
import { RowMenuList } from "src/sections/develop/DatasetListCells";
import DevelopView from "src/sections/develop/DevelopView";
import DevelopDataRightSection from "src/sections/develop-detail/DevelopBarRightSection/DevelopDataRightSection";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";

const h = vi.hoisted(() => ({
  get: vi.fn(), download: vi.fn(), commands: new Map(),
  noop: () => {}, Null: () => null, permitted: true, selected: false, data: null,
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()), default: { get: h.get },
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: h.noop, handleOnDocsClicked: h.noop, Events: {}, PropertyName: {},
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "Owner" }) }));
vi.mock("src/components/iconify", () => ({ default: h.Null }));
vi.mock("src/components/svg-color", () => ({ default: h.Null }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: h.noop }));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: (scope, commands) => h.commands.set(scope, commands),
}));
vi.mock("src/sections/common/ux/useShortcuts", () => ({
  default: h.noop, openShortcutSheet: h.noop,
}));
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({
  default: h.Null, ViewMenu: h.Null,
}));
vi.mock("src/sections/common/ux/oneview/useColumnLayout", () => ({
  default: () => ({ visibleIds: ["name", "actions"] }),
}));
vi.mock("src/components/data-table", () => ({
  // Exercise the real overflow cell and DevelopView's context-menu callback.
  DataTable: ({ data, columns, onRowContextMenu }) => data.map((row) => (
    <div key={row.id}>
      {columns.find((column) => column.id === "actions").cell({ row: { original: row } })}
      <button onClick={(event) => onRowContextMenu(row, event)}>Open context menu</button>
    </div>
  )),
  DataTablePagination: h.Null,
}));
vi.mock("src/sections/develop/hooks/useDatasetListQuery", () => ({
  useDatasetListQuery: () => ({ data: h.data, isLoading: false, isError: false, refetch: h.noop }),
  queryErrorSentence: () => null,
}));
vi.mock("src/sections/develop/hooks/useDatasetStars", () => ({ default: h.noop }));
vi.mock("src/sections/develop/hooks/useGroupCollapse", () => ({ default: () => [new Set(), h.noop] }));
vi.mock("src/sections/develop/organize/useDatasetOrganize", () => ({ default: () => ({ openFor: h.noop }) }));
vi.mock("src/sections/develop/organize/useFolderActions", () => ({ default: () => ({}) }));
vi.mock("src/sections/develop/organize/realFolderApi", () => ({ useRealFolderInventory: () => ({}) }));
vi.mock("src/api/develop/dataset-org", () => ({
  useDatasetStar: () => ({ mutateAsync: h.noop }), errorSentence: () => "Unexpected error",
}));
vi.mock("src/sections/develop/AddDatasetDrawer/AddDatasetDrawer", () => ({ default: h.Null }));
vi.mock("src/sections/develop/DuplicateDataset/DuplicateDataset", () => ({ default: h.Null }));
vi.mock("src/sections/develop/organize/DatasetSettingsDialog", () => ({ default: h.Null }));
vi.mock("src/sections/develop/ListSelectionBar", () => ({ default: h.Null }));
vi.mock("src/sections/develop/organize/DatasetListControls", () => ({
  DatasetFilterChips: h.Null, DatasetListControls: h.Null,
}));
vi.mock("src/sections/develop/organize/FolderRail", () => ({ default: h.Null }));
vi.mock("src/sections/develop/organize/FolderBreadcrumb", () => ({ default: h.Null }));
vi.mock("src/sections/develop/organize/FolderDnd", () => ({
  FolderDndProvider: ({ children }) => <>{children}</>, DatasetDragHandle: h.Null,
}));
vi.mock("src/sections/develop/organize/orgColumns", () => ({ datasetOrgColumns: () => [] }));
vi.mock("src/sections/develop-detail/Common/ConfigureDatasetModal", () => ({ default: h.Null }));
vi.mock("src/sections/develop-detail/BaseColumnsDrawer", () => ({ default: h.Null }));
vi.mock("src/sections/develop-detail/DataTab/Versions/classicWrites", () => ({
  cloneDatasetClassic: h.noop, refusalSentence: h.noop,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { edit: true, run: true, download: h.permitted },
    reason: () => "You need download access to this dataset",
  }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["csv-toolbar", id], queryFn: h.get, enabled: false,
    initialData: { data: { result: { table: [{}], metadata: { dataset_name: "Fixture" } } } },
  }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: h.noop }),
}));
vi.mock("src/sections/develop-detail/DataTab/DataGrid/useDataToolbarView", () => ({
  default: () => ({ gridView: { density: "comfortable", setDensity: h.noop } }),
}));
vi.mock("src/sections/develop-detail/DataTab/DataGrid/useDataGridLayout", () => ({
  default: () => ({ isDefault: true }), DATA_GRID_SURFACE: "grid",
}));
vi.mock("src/sections/develop-detail/DataTab/DataGrid/useDataGridView", () => ({
  getDataGridFilters: () => [{ column_id: "c1", value: "selected" }],
  getDataGridSearch: () => "selected",
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/changeSetStore", () => ({ useChangeSetStore: {} }));
vi.mock("src/sections/develop-detail/DataTab/DatapointDrawerV2/useRowViewUrl", () => ({
  default: () => ({ openRow: h.noop }),
}));
vi.mock("src/sections/develop-detail/AddColumn/AddColumnDrawer", () => ({ default: h.Null }));
vi.mock("src/sections/develop/AddRowDrawer/AddRowDrawer", () => ({ default: h.Null }));
vi.mock("src/sections/develop/AddRowDrawer/EditSyntheticData/SyntheticSummaryDrawer", () => ({ default: h.Null }));
vi.mock("src/sections/develop/AddRowDrawer/EditSyntheticData/state", () => ({
  useEditSyntheticDataStore: () => ({ setOpenSummaryDrawer: h.noop }),
}));
vi.mock("src/sections/common/Values/SchemaDrawer", () => ({ default: h.Null }));
// Only the unsupported selected-row operations are stubbed. The parent owns
// the new all-current-rows fallback and the real FloatingSelectionBar remains.
vi.mock("src/sections/develop-detail/DevelopBarRightSection/DevelopDataSelectionActive", () => ({ default: h.Null }));
vi.mock("src/sections/develop-detail/states", () => ({
  useRunExperimentStore: {},
  useRunPromptStoreShallow: (selector) => selector({ setOpenRunPrompt: h.noop }),
  useAddColumnDrawerStoreShallow: (selector) => selector({ setOpenAddColumnDrawer: h.noop }),
  useRunEvaluationStoreShallow: (selector) => selector({ setOpenRunEvaluation: h.noop }),
  useDevelopSelectedRowsStoreShallow: (selector) => selector({
    selectAll: false, toggledNodes: h.selected ? ["r1"] : [],
  }),
  useDatasetOriginStore: () => ({ processingComplete: true }),
}));

const originalCreate = window.URL.createObjectURL;
const originalRevoke = window.URL.revokeObjectURL;
let client;
let saved;
beforeEach(() => {
  h.permitted = true;
  h.selected = false;
  h.commands.clear();
  h.download.mockReset();
  h.data = { items: [{ id: "dataset-a", name: "Fixture", my_perms: { download: true } }], total: 1 };
  setLaunchOverrideForTest({ launch: true, madeReal: [] });
  setRealBackendForTests(true);
  h.get.mockReset().mockImplementation(async (url, { params }) => {
    expect(url).toContain("/get-dataset-table/");
    return { data: { result: {
      column_config: [{ id: "c1", name: "Question", data_type: "text", origin_type: "OTHERS" }],
      table: params.current_page_index === 0
        ? [{ row_id: "r1", c1: { cell_value: "one" } }, { row_id: "r2", c1: { cell_value: "two" } }]
        : [{ row_id: "r3", c1: { cell_value: "three" } }],
      metadata: { total_rows: 3, has_more: params.current_page_index === 0, dataset_name: "Fixture" },
    } } };
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  saved = [];
  window.URL.createObjectURL = vi.fn((blob) => { saved.push(blob); return "blob:csv-entry"; });
  window.URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(h.noop);
  const noNetwork = () => { throw new Error("Unexpected network outside the row adapter fixture"); };
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
});
afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.URL.createObjectURL = originalCreate;
  window.URL.revokeObjectURL = originalRevoke;
});
const mount = (element, path = "/dashboard/develop") => {
  window.history.replaceState({}, "", path);
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
};
const mountMenu = (permitted = true) => mount(
  <RowMenuList open anchorReference="anchorPosition" anchorPosition={{ top: 20, left: 20 }}
    row={{ ...h.data.items[0], my_perms: { download: permitted } }}
    actions={{ download: h.download }} canEdit canDelete onClose={h.noop} />,
);
const mountToolbar = () => mount(
  <Routes><Route path="/dashboard/develop/:dataset" element={<DevelopDataRightSection />} /></Routes>,
  "/dashboard/develop/dataset-a",
);

it.each([
  { launch: true, real: true }, { launch: true, real: false },
  { launch: false, real: true }, { launch: false, real: false },
])("keeps the shared CSV entry with no advanced claim in build %j", ({ launch, real }) => {
  // MAIN-RULING-EXPORT-CONTRACT supersedes the old hidden-unclaimed assertion.
  setLaunchOverrideForTest({ launch, madeReal: [] });
  setRealBackendForTests(real);
  mountMenu();
  const entry = screen.getByRole("menuitem", { name: "Download…" });
  expect(entry).toBeVisible();
  fireEvent.click(entry);
  expect(h.download).toHaveBeenCalledWith(expect.objectContaining({ id: "dataset-a" }));
});
it("opens the download action when the export alone is claimed", () => {
  setLaunchOverrideForTest({ launch: true, madeReal: ["dataset.export"] });
  mountMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "Download…" }));
  expect(h.download).toHaveBeenCalledWith(expect.objectContaining({ id: "dataset-a" }));
});
it.each([false, true])("retains download permission with the advanced claim %s", (claimed) => {
  setLaunchOverrideForTest({ launch: true, madeReal: claimed ? ["dataset.export"] : [] });
  mountMenu(false);
  expect(screen.getByRole("menuitem", { name: "Download…" })).toHaveAttribute("aria-disabled", "true");
  expect(h.download).not.toHaveBeenCalled();
});

async function openEntry(kind) {
  if (kind.startsWith("list")) {
    mount(<DevelopView />);
    fireEvent.click(screen.getByRole("button", {
      name: kind === "list-overflow" ? "Fixture actions" : "Open context menu",
    }));
  } else {
    h.selected = kind === "grid-selection";
    mountToolbar();
    if (kind === "grid-selection") return screen.getByTestId("selection-download-all-rows");
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
  }
  return screen.findByRole("menuitem", { name: /^Download…(?: You need download access to this dataset)?$/ });
}
async function expectCsvDownload() {
  expect(screen.getByTestId("dataset-export-dialog")).toBeVisible();
  expect(screen.getAllByRole("radio").map((input) => input.value)).toEqual(["csv"]);
  await waitFor(() => expect(screen.getByTestId("export-download")).toBeEnabled());
  expect(screen.getByTestId("export-file-name").textContent).toMatch(/\.csv$/);
  expect(screen.getByText("All current rows")).toBeVisible();
  expect(screen.queryByRole("combobox", { name: "Rows" })).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "Version" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByTestId("export-download"));
  expect(saved).toHaveLength(1);
  expect(saved[0].type).toBe("text/csv");
  const text = await new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsText(saved[0]);
  });
  for (const value of ["one", "two", "three"]) expect(text).toContain(value);
  // The real adapter paginates every row, even from the selection fallback.
  // No advanced/version/split endpoint or requested format can slip through.
  expect(h.get).toHaveBeenCalledTimes(2);
  expect(h.get.mock.calls.map(([, { params }]) => params.current_page_index)).toEqual([0, 1]);
  for (const [url, { params }] of h.get.mock.calls) {
    expect(url).toContain("/get-dataset-table/");
    expect(params.filters).toBe("[]");
    expect(params.search).toBeUndefined();
    expect(params).not.toHaveProperty("format");
  }
}
const entries = ["list-overflow", "list-context", "grid-more", "grid-selection"];
it.each(entries)("%s opens the real CSV dialog and downloads current rows with the advanced gate closed", async (kind) => {
  // This traverses each real parent callback; mounting open=true would miss
  // DevelopView's discarded callback and the grid's discarded toolbar action.
  const entry = await openEntry(kind);
  expect(entry).toBeVisible();
  expect(h.get).not.toHaveBeenCalled();
  expect(screen.queryByTestId("dataset-export-dialog")).not.toBeInTheDocument();
  fireEvent.click(entry);
  await expectCsvDownload();
});
it.each(entries)("%s keeps download permission with the advanced gate closed", async (kind) => {
  h.permitted = false;
  h.data.items[0].my_perms.download = false;
  const entry = await openEntry(kind);
  if (kind === "grid-selection") expect(entry).toBeDisabled();
  else expect(entry).toHaveAttribute("aria-disabled", "true");
  expect(screen.queryByTestId("dataset-export-dialog")).not.toBeInTheDocument();
  expect(h.get).not.toHaveBeenCalled();
  expect(saved).toEqual([]);
});
it("keeps the permission-checked grid command palette CSV action when advanced export is closed", async () => {
  mountToolbar();
  const command = h.commands.get("grid.actions").find((item) => item.id === "grid.action.download");
  expect(command.when()).toBe(true);
  expect(h.get).not.toHaveBeenCalled();
  act(() => command.run());
  await expectCsvDownload();
});
it("does not enable the CSV command palette action without download permission", () => {
  h.permitted = false;
  mountToolbar();
  const command = h.commands.get("grid.actions").find((item) => item.id === "grid.action.download");
  expect(command.when()).toBe(false);
  act(() => command.run());
  expect(h.get).not.toHaveBeenCalled();
  expect(screen.queryByTestId("dataset-export-dialog")).not.toBeInTheDocument();
});
