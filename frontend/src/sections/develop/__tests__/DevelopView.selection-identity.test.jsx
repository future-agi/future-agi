import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "src/utils/test-utils";
import { evalPresentationTheme } from "src/sections/evals/components/__tests__/evalPresentationTheme";
import DevelopView from "../DevelopView";

const h = vi.hoisted(() => ({
  data: null, delete: vi.fn(), notify: vi.fn(), finishUndo: null,
  noop: () => {}, Null: () => null,
}));
vi.mock("src/utils/axios", () => ({
  default: { delete: h.delete },
  endpoints: { develop: { deleteDataset: () => "/datasets/delete" } },
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: h.noop, handleOnDocsClicked: h.noop, Events: {}, PropertyName: {},
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "Owner" }) }));
vi.mock("src/components/iconify", () => ({ default: h.Null }));
vi.mock("src/components/svg-color", () => ({ default: h.Null }));
vi.mock("../hooks/useDatasetListQuery", () => ({
  useDatasetListQuery: () => ({ data: h.data, isLoading: false, isError: false, refetch: h.noop }),
  queryErrorSentence: () => null,
}));
vi.mock("../realBackendCaps", async (original) => ({
  ...(await original()),
  datasetCaps: () => ({ real: true, searchScope: "name" }),
}));
vi.mock("../hooks/useDatasetStars", () => ({ default: h.noop }));
vi.mock("../hooks/useGroupCollapse", () => ({ default: () => [[], h.noop] }));
vi.mock("../organize/useDatasetOrganize", () => ({ default: () => ({ openFor: h.noop }) }));
vi.mock("../organize/useFolderActions", () => ({ default: () => ({}) }));
vi.mock("../organize/realFolderApi", () => ({ useRealFolderInventory: () => ({}) }));
vi.mock("src/api/develop/dataset-org", () => ({
  useDatasetStar: () => ({ mutateAsync: h.noop }), errorSentence: () => "Unexpected error",
}));
vi.mock("src/sections/common/ux/oneview/useColumnLayout", () => ({
  default: () => ({ columns: [], catalog: [], visibleIds: ["name"], canMoveBy: () => ({ ok: false }) }),
}));
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({ ViewMenu: h.Null }));
vi.mock("src/sections/common/ux/viewState", async () => {
  const { useCallback, useState } = await import("react");
  return { useViewUrlState: (defaults) => {
    const [view, setView] = useState(defaults);
    const update = useCallback((patch) => setView((previous) => ({ ...previous, ...patch })), []);
    return [view, update];
  } };
});
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: h.noop, openShortcutSheet: h.noop }));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({ useRegisterCommands: h.noop }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: h.notify }));
vi.mock("src/sections/develop-detail/states", () => ({ useRunExperimentStore: {} }));
vi.mock("src/sections/develop-detail/Common/ConfigureDatasetModal", () => ({ default: h.Null }));
vi.mock("src/sections/develop-detail/BaseColumnsDrawer", () => ({ default: h.Null }));
vi.mock("src/sections/develop-detail/DataTab/Versions/classicWrites", () => ({
  cloneDatasetClassic: h.noop, refusalSentence: h.noop,
}));
vi.mock("src/sections/common/export/ExportDialog", () => ({ DatasetExportDialog: h.Null }));
vi.mock("../AddDatasetDrawer/AddDatasetDrawer", () => ({ default: h.Null }));
vi.mock("../DuplicateDataset/DuplicateDataset", () => ({ default: h.Null }));
vi.mock("../organize/DatasetSettingsDialog", () => ({ default: h.Null }));
vi.mock("../DatasetListCells", () => ({ RowMenuList: h.Null }));
vi.mock("../datasetListColumns", () => ({
  datasetListColumns: () => [{ id: "name", accessorKey: "name", header: "Name" }],
}));
vi.mock("../organize/DatasetListControls", () => ({ DatasetFilterChips: h.Null, DatasetListControls: h.Null }));
vi.mock("../organize/FolderRail", () => ({ default: h.Null }));
vi.mock("../organize/FolderBreadcrumb", () => ({ default: h.Null }));
vi.mock("../organize/orgColumns", () => ({ datasetOrgColumns: () => [] }));

/* eslint-disable react/prop-types */
vi.mock("src/sections/common/ux/KeyHintTooltip", () => ({ default: ({ children }) => children }));
vi.mock("../organize/FolderDnd", () => ({ FolderDndProvider: ({ children }) => children }));
// Keep the actual table identity adapter and the actual confirmation/delete
// handler. Only the visual grid and unrelated panels are substituted.
vi.mock("src/components/data-table", async () => ({
  DataTable: (await import("src/components/data-table/DataTable")).default,
  DataTablePagination: (await import("src/components/data-table/DataTablePagination")).default,
}));
vi.mock("@mui/x-data-grid", () => ({
  DataGrid: ({ rows, getRowId, rowSelectionModel, onRowSelectionModelChange }) => (
    <section aria-label="Dataset rows">{rows.map((row) => (
      <label key={getRowId(row)}>
        <input type="checkbox" checked={rowSelectionModel.includes(getRowId(row))}
          onChange={(event) => onRowSelectionModelChange(event.target.checked
            ? [...rowSelectionModel, getRowId(row)]
            : rowSelectionModel.filter((id) => id !== getRowId(row)))} />
        {row.name}
      </label>
    ))}</section>
  ),
}));
/* eslint-enable react/prop-types */

const A = { id: "dataset-a", name: "Dataset A" };
const B = { id: "unticked-b", name: "Unticked B" };
const C = { id: "dataset-c", name: "Dataset C" };
const D = { id: "unticked-d", name: "Unticked D" };
let client;
const setRows = (items) => { h.data = { items, total: 50 }; };
function mount() {
  const ui = () => <QueryClientProvider client={client}><DevelopView /></QueryClientProvider>;
  const view = render(ui(), { theme: evalPresentationTheme("light") });
  return (rows) => { setRows(rows); view.rerender(ui()); };
}
const tick = (row) => fireEvent.click(screen.getByLabelText(row.name));

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  setRows([A, B, C]);
  h.delete.mockReset().mockResolvedValue({ data: {} });
  h.finishUndo = null;
  h.notify.mockReset().mockImplementation(() => ({
    undone: new Promise((resolve) => { h.finishUndo = resolve; }),
  }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});
afterEach(() => { cleanup(); client.clear(); });

describe("c26 dataset list stable selection", () => {
  it("DS-REORDER deletes only checked IDs and freezes the confirmation through reorder and untick", async () => {
    const refresh = mount();
    tick(A);
    refresh([B, A, C]);
    expect(screen.getByLabelText(A.name)).toBeChecked();
    expect(screen.getByLabelText(B.name)).not.toBeChecked();
    tick(C);
    fireEvent.click(screen.getByRole("button", { name: "Delete 2", exact: true }));
    const dialog = screen.getByRole("dialog");
    refresh([D, C, B, A]);
    tick(C);
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete 2", exact: true }));
    expect(h.delete).not.toHaveBeenCalled();
    await act(async () => h.finishUndo(false));
    await waitFor(() => expect(h.delete).toHaveBeenCalledTimes(1));
    expect(h.delete).toHaveBeenCalledWith("/datasets/delete", {
      data: { dataset_ids: [A.id, C.id] },
    });
  });

  it("DS-PAGE clears selected IDs even when a cached page has the same rows", () => {
    mount();
    tick(A);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByLabelText(A.name)).not.toBeChecked();
    expect(screen.queryByRole("button", { name: /^Delete \d+$/ })).not.toBeInTheDocument();
    expect(h.delete).not.toHaveBeenCalled();
  });

  it("DS-SEARCH clears before the debounce replaces the query", () => {
    mount();
    tick(A);
    fireEvent.change(screen.getByRole("textbox", { name: "Search datasets" }), { target: { value: "other" } });
    expect(screen.getByLabelText(A.name)).not.toBeChecked();
    expect(h.delete).not.toHaveBeenCalled();
  });

  it("DS-PRUNE does not revive an absent ID when a later poll returns it", () => {
    const refresh = mount();
    tick(A);
    refresh([B, C]);
    refresh([A, B, C]);
    expect(screen.getByLabelText(A.name)).not.toBeChecked();
    expect(screen.getByLabelText(B.name)).not.toBeChecked();
  });
});
