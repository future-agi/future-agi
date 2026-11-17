// FR-DATASETS-B01: a brand-new workspace (no datasets) opens on the first-run
// screen even when folders are on, and "No datasets on this page" is left for
// a workspace that has datasets. Data hooks and unrelated panels are stubbed;
// the page, its empty states and the shared empty-state components are real.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  within,
  waitFor,
} from "src/utils/test-utils";
import DevelopView from "./DevelopView";

const h = vi.hoisted(() => {
  // Staging today: the real backend with the folder rail and stars on. Set
  // here so the test does not depend on the build's launch switch.
  const base = Object.freeze({
    real: true,
    listFolders: true,
    listStars: true,
    searchScope: "name",
  });
  return {
    noop: () => {},
    Null: () => null,
    request: vi.fn(),
    listQuery: vi.fn(),
    columns: vi.fn(),
    rail: vi.fn(),
    base,
    caps: base,
    data: null,
    inventory: null,
  };
});

vi.mock("./realBackendCaps", async (importOriginal) => ({
  ...(await importOriginal()),
  datasetCaps: () => h.caps,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: h.request, post: h.request, delete: h.request },
  endpoints: {},
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: h.noop,
  handleOnDocsClicked: h.noop,
  Events: {},
  PropertyName: {},
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/components/iconify", () => ({ default: h.Null }));
vi.mock("src/components/svg-color", () => ({ default: h.Null }));
vi.mock("./hooks/useDatasetListQuery", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetListQuery: (args) => {
    h.listQuery(args);
    return {
      data: h.data,
      isLoading: false,
      isError: false,
      refetch: h.noop,
    };
  },
  queryErrorSentence: () => null,
}));
vi.mock("./organize/realFolderApi", () => ({
  useRealFolderInventory: () => ({
    data: h.inventory,
    isError: false,
    refetch: h.noop,
  }),
}));
vi.mock("./hooks/useDatasetStars", () => ({ default: h.noop }));
vi.mock("./hooks/useGroupCollapse", () => ({
  default: () => [new Set(), h.noop],
}));
vi.mock("./organize/useDatasetOrganize", () => ({
  default: () => ({ openFor: h.noop }),
}));
vi.mock("./organize/useFolderActions", () => ({
  default: () => ({ open: h.noop }),
}));
vi.mock("src/api/develop/dataset-org", () => ({
  useDatasetStar: () => ({ mutateAsync: h.request }),
  errorSentence: () => "Unexpected error",
}));
vi.mock("src/sections/common/ux/oneview/useColumnLayout", () => ({
  default: () => ({ visibleIds: ["name"] }),
}));
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({
  ViewMenu: h.Null,
}));
vi.mock("src/sections/common/ux/useShortcuts", () => ({
  default: h.noop,
  openShortcutSheet: h.noop,
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: h.noop,
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: h.noop }));
vi.mock("src/sections/develop-detail/states", () => ({
  useRunExperimentStore: {},
}));
vi.mock("src/sections/develop-detail/Common/ConfigureDatasetModal", () => ({
  default: h.Null,
}));
vi.mock("src/sections/develop-detail/BaseColumnsDrawer", () => ({
  default: h.Null,
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/classicWrites", () => ({
  cloneDatasetClassic: h.request,
  refusalSentence: h.noop,
}));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  DatasetExportDialog: h.Null,
}));
vi.mock("./DuplicateDataset/DuplicateDataset", () => ({ default: h.Null }));
vi.mock("./organize/DatasetSettingsDialog", () => ({ default: h.Null }));
vi.mock("./DatasetListCells", () => ({ RowMenuList: h.Null }));
vi.mock("./datasetListColumns", async (importOriginal) => ({
  ...(await importOriginal()),
  datasetListColumns: (args) => {
    h.columns(args);
    return [];
  },
}));
vi.mock("./organize/DatasetListControls", () => ({
  DatasetFilterChips: h.Null,
  DatasetListControls: h.Null,
}));
vi.mock("./organize/FolderBreadcrumb", () => ({ default: h.Null }));
vi.mock("./organize/orgColumns", () => ({ datasetOrgColumns: () => [] }));
vi.mock("./organize/FolderRail", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (props) => {
    h.rail(props);
    return <nav data-testid="folder-rail" aria-label="Folders" />;
  },
}));

/* eslint-disable react/prop-types */
vi.mock("src/sections/common/ux/KeyHintTooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("./organize/FolderDnd", () => ({
  FolderDndProvider: ({ children }) => <>{children}</>,
}));
vi.mock("./AddDatasetDrawer/AddDatasetDrawer", () => ({
  default: ({ open }) =>
    open ? <div data-testid="add-dataset-drawer">New dataset drawer</div> : null,
}));
// The grid itself is not under test: like the real one, it shows the page's
// empty content when it has no rows.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, emptyContent }) => (
    <div data-testid="datasets-table">
      {data.length
        ? data.map((row) => <div key={row.id}>{row.name}</div>)
        : emptyContent}
    </div>
  ),
  DataTablePagination: () => null,
}));
/* eslint-enable react/prop-types */

const FIRST_RUN_BODY =
  /^A dataset holds the tasks you test your prompt or agent on\. /;

let client;
const mount = (route = "/dashboard/develop") =>
  renderWithRouter(
    <QueryClientProvider client={client}>
      <DevelopView />
    </QueryClientProvider>,
    { route },
  );

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  h.listQuery.mockClear();
  h.columns.mockClear();
  h.rail.mockClear();
  h.request.mockReset().mockImplementation(() => {
    throw new Error("Network is forbidden in this test");
  });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  client.setQueryData(["capabilities"], {
    data: { features: { dataset_stars: { allowed: true } } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  h.caps = h.base;
  h.data = null;
  h.inventory = null;
});

describe("FR-DATASETS-B01 datasets first-run screen with folders on", () => {
  it("FR-DATASETS-B01-a a new workspace opens on the first-run screen and New dataset opens the create drawer", async () => {
    h.data = { items: [], total: 0, folders: [] };
    h.inventory = { folders: [], all_count: 0, starred_count: 0 };
    mount();

    expect(screen.getByText("Add your first dataset")).toBeVisible();
    expect(screen.getByText(FIRST_RUN_BODY)).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Read the datasets guide" }),
    ).toHaveAttribute("href", "https://docs.futureagi.com/docs/dataset");
    // No folder rail, folder door or page-level sentence in an empty workspace.
    expect(screen.queryByText("No datasets on this page")).toBeNull();
    expect(screen.queryByTestId("folder-rail")).toBeNull();
    expect(screen.queryByRole("button", { name: "New folder" })).toBeNull();
    expect(screen.queryByTestId("datasets-table")).toBeNull();
    // The SDK's get_dataset_config reads an existing dataset by name, which a
    // new workspace does not have, so the first-run screen does not show it.
    expect(screen.queryByText(/get_dataset_config/)).toBeNull();

    const create = screen.getByRole("button", { name: "New dataset" });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    expect(await screen.findByTestId("add-dataset-drawer")).toBeVisible();
    expect(h.request).not.toHaveBeenCalled();
  });

  it("FR-DATASETS-B01-b folders without datasets keep the rail and show the first-run state in the list", () => {
    h.data = { items: [], total: 0, folders: [] };
    h.inventory = {
      folders: [{ id: "folder-1", path: "support", count: 0, total: 0 }],
      all_count: 0,
      starred_count: 0,
    };
    mount();

    expect(screen.getByTestId("folder-rail")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New folder" })).toBeEnabled();
    const list = screen.getByTestId("datasets-table");
    expect(within(list).getByText("Add your first dataset")).toBeVisible();
    expect(within(list).getByText(FIRST_RUN_BODY)).toBeVisible();
    expect(
      within(list).getByRole("button", { name: "New dataset" }),
    ).toBeEnabled();
    expect(screen.queryByText("No datasets on this page")).toBeNull();
  });

  it("FR-DATASETS-B01-c a workspace with datasets keeps the page-level empty text", () => {
    h.data = { items: [], total: 30, folders: [] };
    h.inventory = { folders: [], all_count: 30, starred_count: 0 };
    mount();

    const list = screen.getByTestId("datasets-table");
    expect(within(list).getByText("No datasets on this page")).toBeVisible();
    expect(screen.queryByText("Add your first dataset")).toBeNull();
    expect(screen.getByTestId("folder-rail")).toBeInTheDocument();
  });
});

// Starred is one control (datasets-list-toolbar.test.jsx): the folder rail's
// row while the rail shows, else a toolbar toggle. Each W19 case checks the
// toolbar toggle with the rail off and the rail's Starred row with it on.
describe("W19 dataset list star availability", () => {
  const NOT_ALLOWED = {
    data: { features: { dataset_stars: { allowed: false } } },
  };
  const starredParam = () =>
    new URLSearchParams(window.location.search).get("starred");
  const neverListedStarred = () =>
    h.listQuery.mock.calls.every(([args]) => args.starred !== true);

  beforeEach(() => {
    h.data = {
      items: [{ id: "dataset-a", name: "Golden", starred: false }],
      total: 1,
    };
    h.inventory = { folders: [], all_count: 1, starred_count: 0 };
  });

  it("W19-06 refuses unsupported Starred links, filters and row actions", async () => {
    h.caps = { ...h.base, listFolders: false };
    client.setQueryData(["capabilities"], NOT_ALLOWED);
    mount("/dashboard/develop?starred=true");

    expect(screen.queryByTestId("folder-rail")).toBeNull();
    const starred = screen.getByRole("button", { name: "Starred" });
    expect(starred).toBeDisabled();
    expect(starred).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByText("Stars are not available on this server."),
    ).toBeVisible();
    expect(screen.getByText("Golden")).toBeVisible();
    expect(screen.queryByText("No starred datasets match")).toBeNull();
    expect(neverListedStarred()).toBe(true);
    expect(h.columns.mock.lastCall[0].rowActions.star).toBeUndefined();
    await waitFor(() => expect(starredParam()).not.toBe("true"));
    expect(h.request).not.toHaveBeenCalled();
  });

  it("W19-06-rail with the folder rail, an unsupported server gets no Starred row or toggle and the link is refused", async () => {
    client.setQueryData(["capabilities"], NOT_ALLOWED);
    mount("/dashboard/develop?starred=true");

    expect(screen.getByTestId("folder-rail")).toBeInTheDocument();
    expect(h.rail.mock.lastCall[0].starsAvailable).toBe(false);
    expect(h.rail.mock.lastCall[0].pick).not.toEqual({ kind: "starred" });
    expect(screen.queryByRole("button", { name: "Starred" })).toBeNull();
    expect(screen.getByText("Golden")).toBeVisible();
    expect(screen.queryByText("No starred datasets match")).toBeNull();
    expect(neverListedStarred()).toBe(true);
    expect(h.columns.mock.lastCall[0].rowActions.star).toBeUndefined();
    await waitFor(() => expect(starredParam()).not.toBe("true"));
    expect(h.request).not.toHaveBeenCalled();
  });

  it("W19-07 retains the supported server Starred filter and row action", () => {
    h.caps = { ...h.base, listFolders: false };
    mount("/dashboard/develop?starred=true");

    const starred = screen.getByRole("button", { name: "Starred" });
    expect(starred).toBeEnabled();
    expect(starred).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.queryByText("Stars are not available on this server."),
    ).toBeNull();
    expect(h.listQuery).toHaveBeenCalledWith(
      expect.objectContaining({ starred: true }),
    );
    expect(h.columns.mock.lastCall[0].rowActions.star).toEqual(
      expect.any(Function),
    );
    expect(h.request).not.toHaveBeenCalled();
  });

  it("W19-07-rail with the folder rail, a supported server keeps the rail's Starred pick", () => {
    mount("/dashboard/develop?starred=true");

    expect(h.rail.mock.lastCall[0].starsAvailable).toBe(true);
    expect(h.rail.mock.lastCall[0].pick).toEqual({ kind: "starred" });
    // One Starred control: the rail's, not a second one in the toolbar.
    expect(screen.queryByRole("button", { name: "Starred" })).toBeNull();
    expect(h.listQuery).toHaveBeenCalledWith(
      expect.objectContaining({ starred: true }),
    );
    expect(h.columns.mock.lastCall[0].rowActions.star).toEqual(
      expect.any(Function),
    );
    expect(h.request).not.toHaveBeenCalled();
  });
});
