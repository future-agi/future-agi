// The Datasets list toolbar is one row of compact controls: search, Filter,
// `Group by: <value>` (a menu button, not a select with a floating label),
// View, then New folder and New dataset. Starred is one control: the folder
// rail's row while the rail shows, else a toolbar toggle that says whether
// it is on. Data hooks and unrelated panels are stubbed; the page and its
// Filter / Group by controls are real.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
} from "src/utils/test-utils";
import DevelopView from "../DevelopView";

const h = vi.hoisted(() => {
  // The real backend with stars, folders, Filter and Group by on; each case
  // turns what it needs off.
  const base = Object.freeze({
    real: true,
    listStars: true,
    listFolders: true,
    listFilters: true,
    listGroupBy: true,
    searchScope: "name",
  });
  return {
    noop: () => {},
    Null: () => null,
    request: vi.fn(),
    base,
    caps: base,
    data: null,
    inventory: null,
  };
});

vi.mock("../realBackendCaps", async (importOriginal) => ({
  ...(await importOriginal()),
  datasetCaps: () => h.caps,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: h.request,
      post: h.request,
      delete: h.request,
    },
  };
});
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: h.noop,
  handleOnDocsClicked: h.noop,
}));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: h.Null,
}));
vi.mock("../hooks/useDatasetListQuery", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetListQuery: () => ({
    data: h.data,
    isLoading: false,
    isError: false,
    refetch: h.noop,
  }),
  queryErrorSentence: () => null,
}));
vi.mock("../organize/realFolderApi", async (importOriginal) => ({
  ...(await importOriginal()),
  useRealFolderInventory: () => ({
    data: h.inventory,
    isError: false,
    refetch: h.noop,
  }),
}));
vi.mock("../hooks/useDatasetStars", async (importOriginal) => ({
  ...(await importOriginal()),
  default: h.noop,
}));
vi.mock("../hooks/useGroupCollapse", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => [new Set(), h.noop],
}));
vi.mock("../organize/useDatasetOrganize", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({ openFor: h.noop }),
}));
vi.mock("../organize/useFolderActions", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({ open: h.noop }),
}));
vi.mock("src/api/develop/dataset-org", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetStar: () => ({ mutateAsync: h.request }),
  errorSentence: () => "Unexpected error",
}));
vi.mock(
  "src/sections/common/ux/oneview/useColumnLayout",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => ({ visibleIds: ["name"] }),
  }),
);
vi.mock("src/sections/common/ux/oneview/ViewMenu", async (importOriginal) => ({
  ...(await importOriginal()),
  ViewMenu: h.Null,
}));
vi.mock("src/sections/common/ux/useShortcuts", async (importOriginal) => ({
  ...(await importOriginal()),
  default: h.noop,
  openShortcutSheet: h.noop,
}));
vi.mock(
  "src/sections/common/CommandPalette/commandRegistry",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRegisterCommands: h.noop,
  }),
);
vi.mock("src/sections/common/ux/toast", async (importOriginal) => ({
  ...(await importOriginal()),
  notify: h.noop,
}));
vi.mock("src/sections/develop-detail/Common/ConfigureDatasetModal", () => ({
  default: h.Null,
}));
vi.mock("src/sections/develop-detail/BaseColumnsDrawer", () => ({
  default: h.Null,
}));
vi.mock("src/sections/common/export/ExportDialog", async (importOriginal) => ({
  ...(await importOriginal()),
  DatasetExportDialog: h.Null,
}));
vi.mock("../DuplicateDataset/DuplicateDataset", () => ({ default: h.Null }));
vi.mock("../organize/DatasetSettingsDialog", () => ({ default: h.Null }));
vi.mock("../datasetListColumns", async (importOriginal) => ({
  ...(await importOriginal()),
  datasetListColumns: () => [],
}));
vi.mock("../organize/FolderBreadcrumb", () => ({ default: h.Null }));
vi.mock("../organize/FolderRail", () => ({
  default: () => <nav data-testid="folder-rail" aria-label="Folders" />,
}));

/* eslint-disable react/prop-types */
vi.mock("src/sections/common/ux/KeyHintTooltip", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ children }) => <>{children}</>,
}));
vi.mock("../organize/FolderDnd", async (importOriginal) => ({
  ...(await importOriginal()),
  FolderDndProvider: ({ children }) => <>{children}</>,
}));
// The grid itself is not under test.
vi.mock("src/components/data-table", async (importOriginal) => ({
  ...(await importOriginal()),
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

const HELLO = { id: "d-hello", name: "hello", numberOfDatapoints: 5 };

let client;
const mount = () =>
  renderWithRouter(
    <QueryClientProvider client={client}>
      <DevelopView />
    </QueryClientProvider>,
    { route: "/dashboard/develop" },
  );

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  h.request.mockReset().mockImplementation(() => {
    throw new Error("Network is forbidden in this test");
  });
  h.data = { items: [HELLO], total: 1, folders: [] };
  h.inventory = {
    folders: [{ id: "folder-1", path: "hello", count: 0, total: 0 }],
    all_count: 1,
    starred_count: 0,
  };
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  // The page asks the server whether it can store stars (W19); this server
  // can, so the answer is seeded and nothing reaches the network.
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

describe("Datasets list toolbar", () => {
  it("with the folder rail, Starred is the rail's row and Group by is a compact menu button", () => {
    h.caps = h.base;
    mount();

    expect(screen.getByTestId("folder-rail")).toBeInTheDocument();
    // One Starred control: the rail's, not a second one in the toolbar.
    expect(screen.queryByRole("button", { name: "Starred" })).toBeNull();

    expect(
      screen.getByRole("textbox", { name: "Search datasets" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filter" })).toBeInTheDocument();
    const group = screen.getByRole("button", { name: "Group by: None" });
    expect(group).toHaveAttribute("aria-haspopup", "menu");
    expect(group).toHaveAttribute("aria-expanded", "false");
    // No select with a floating label in the toolbar.
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByRole("button", { name: "New folder" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "New dataset" })).toBeEnabled();
    expect(h.request).not.toHaveBeenCalled();
  });

  it("without the rail, Starred is a toolbar toggle that shows its state", () => {
    h.caps = { ...h.base, listFolders: false };
    mount();

    expect(screen.queryByTestId("folder-rail")).toBeNull();
    expect(screen.queryByRole("button", { name: "New folder" })).toBeNull();
    const starred = screen.getByRole("button", { name: "Starred" });
    expect(starred).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(starred);
    expect(screen.getByRole("button", { name: "Starred" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "Starred" }));
    expect(screen.getByRole("button", { name: "Starred" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(h.request).not.toHaveBeenCalled();
  });

  it("a build without stars has no Starred toggle at all", () => {
    h.caps = { ...h.base, listFolders: false, listStars: false };
    mount();

    expect(screen.queryByRole("button", { name: "Starred" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Group by: None" }),
    ).toBeInTheDocument();
  });
});
