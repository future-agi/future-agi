// The Datasets list's Filter and Group by on this server (get-datasets
// applies no filter or grouping). Both controls stay on screen and list
// every entry, each turned off with its reason: Agent says the list can't be
// filtered or grouped by agent, each metadata key the rows carry says the
// same for metadata, and every other entry keeps the shared line. Choosing
// one changes nothing and sends nothing. The rows carry metadata, agent_ids
// and agents as get-datasets answers them once datasets save them. Data
// hooks and unrelated panels are stubbed; the page and its Filter / Group by
// controls are the real components.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  within,
} from "src/utils/test-utils";
import DevelopView from "../DevelopView";

const h = vi.hoisted(() => {
  // The real backend with stars, folders, Filter and Group by on.
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

// Rows as get-datasets answers them once datasets save metadata and agents.
const GOLDEN = {
  id: "gs",
  name: "Golden",
  numberOfDatapoints: 5,
  metadata: { team: "support", domain: "returns" },
  agent_ids: ["g1", "g2"],
  agents: [
    { id: "g1", name: "Support Agent" },
    { id: "g2", name: "Returns Agent" },
  ],
};
const REFUNDS = {
  id: "rp",
  name: "Refund policy QA",
  numberOfDatapoints: 12,
  metadata: { tier: 2 },
  agent_ids: [],
  agents: [],
};
// The list's answer on this server: no groups, facets or key types.
const listOf = (items) => ({
  items,
  total: items.length,
  totalPages: 1,
  groups: null,
  folders: [],
  facets: {},
  leftOut: {},
});

const AGENT = "This list can't be filtered or grouped by agent.";
const METADATA = "This list can't be filtered or grouped by metadata.";
const LINE = "Not available in this release";
const NOT_THESE_WORDS = /isn't available|Not available in this release/;
const KEYS = ["domain", "team", "tier"];
const FILTER_OTHERS = [
  "Name",
  "Kind",
  "Source",
  "Owner",
  "Tag",
  "Folder",
  "Starred",
  "Rows",
  "Experiments",
  "Updated",
  "Created",
  "Baseline",
  "Best",
];
const GROUP_OTHERS = ["Kind", "Source", "Owner", "Tag", "Folder"];

let client;
const mount = (route = "/dashboard/develop") =>
  renderWithRouter(
    <QueryClientProvider client={client}>
      <DevelopView />
    </QueryClientProvider>,
    { route },
  );
const openFilter = () => {
  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  return screen.getByRole("menu", { name: "Filter datasets" });
};
const openGroupBy = () => {
  fireEvent.click(screen.getByRole("button", { name: "Group by: None" }));
  return screen.getByRole("menu", { name: "Group by: None" });
};
const expectTurnedOff = (entry, reason) => {
  expect(entry).toBeVisible();
  expect(entry).toHaveAttribute("aria-disabled", "true");
  expect(entry).toHaveAccessibleDescription(reason);
  expect(entry).toHaveTextContent(reason);
};

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  h.request.mockReset().mockImplementation(() => {
    throw new Error("Network is forbidden in this test");
  });
  h.data = listOf([GOLDEN, REFUNDS]);
  h.inventory = {
    folders: [{ id: "folder-1", path: "support", count: 0, total: 0 }],
    all_count: 2,
    starred_count: 0,
  };
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  // The page asks the server whether it can store stars; this server can,
  // so the answer is seeded and nothing reaches the network.
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

describe("the list's Filter on this server", () => {
  it("lists Agent, visible, turned off and described by its sentence", () => {
    mount();
    const menu = openFilter();

    expectTurnedOff(
      within(menu).getByRole("menuitem", { name: "Agent" }),
      AGENT,
    );
  });

  it("lists one entry per metadata key the rows carry, each described by the metadata sentence", () => {
    mount();
    const menu = openFilter();

    for (const key of KEYS)
      expectTurnedOff(
        within(menu).getByRole("menuitem", { name: key }),
        METADATA,
      );
  });

  it("keeps every other entry, each turned off with the shared line", () => {
    mount();
    const menu = openFilter();

    for (const name of FILTER_OTHERS)
      expectTurnedOff(within(menu).getByRole("menuitem", { name }), LINE);
    // Nothing hidden, nothing added.
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(
      FILTER_OTHERS.length + 1 + KEYS.length,
    );
  });

  it("choosing Agent or a metadata key changes nothing and sends no request", () => {
    mount();
    const before = window.location.search;
    const menu = openFilter();

    for (const name of ["Agent", ...KEYS])
      fireEvent.click(within(menu).getByRole("menuitem", { name }));

    expect(window.location.search).toBe(before);
    expect(screen.queryByTestId("filter-chip")).toBeNull();
    expect(screen.getByText("Golden")).toBeVisible();
    expect(screen.getByText("Refund policy QA")).toBeVisible();
    expect(h.request).not.toHaveBeenCalled();
  });

  it("never says isn't available or Not available in this release on the agent and metadata entries", () => {
    mount();
    const menu = openFilter();

    for (const name of ["Agent", ...KEYS]) {
      const entry = within(menu).getByRole("menuitem", { name });
      expect(entry.textContent).not.toMatch(NOT_THESE_WORDS);
      expect(entry).not.toHaveAccessibleDescription(NOT_THESE_WORDS);
    }
  });

  it("lists Agent and no metadata key where the rows carry no metadata", () => {
    h.data = listOf([{ id: "d-hello", name: "hello", numberOfDatapoints: 5 }]);
    mount();
    const menu = openFilter();

    expectTurnedOff(
      within(menu).getByRole("menuitem", { name: "Agent" }),
      AGENT,
    );
    for (const key of KEYS)
      expect(within(menu).queryByRole("menuitem", { name: key })).toBeNull();
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(
      FILTER_OTHERS.length + 1,
    );
  });
});

describe("the list's Group by on this server", () => {
  it("lists Agent and each metadata key, visible, turned off and described by their sentences", () => {
    mount();
    const menu = openGroupBy();

    const agent = within(menu).getByRole("menuitemradio", { name: "Agent" });
    expectTurnedOff(agent, AGENT);
    expect(agent).toHaveAttribute("aria-checked", "false");
    for (const key of KEYS)
      expectTurnedOff(
        within(menu).getByRole("menuitemradio", { name: key }),
        METADATA,
      );
  });

  it("keeps None checked and every other option turned off with the shared line", () => {
    mount();
    const menu = openGroupBy();

    const none = within(menu).getByRole("menuitemradio", { name: "None" });
    expect(none).toHaveAttribute("aria-checked", "true");
    expect(none).not.toHaveAttribute("aria-disabled");
    for (const name of GROUP_OTHERS)
      expectTurnedOff(within(menu).getByRole("menuitemradio", { name }), LINE);
    // Nothing hidden, nothing added.
    expect(within(menu).getAllByRole("menuitemradio")).toHaveLength(
      1 + GROUP_OTHERS.length + 1 + KEYS.length,
    );
  });

  it("choosing Agent or a metadata key changes nothing and sends no request", () => {
    mount();
    const before = window.location.search;
    const group = screen.getByRole("button", { name: "Group by: None" });
    const menu = openGroupBy();

    for (const name of ["Agent", ...KEYS])
      fireEvent.click(within(menu).getByRole("menuitemradio", { name }));

    expect(window.location.search).toBe(before);
    expect(group).toHaveTextContent("Group by: None");
    expect(screen.getByText("Golden")).toBeVisible();
    expect(screen.getByText("Refund policy QA")).toBeVisible();
    expect(h.request).not.toHaveBeenCalled();
  });

  it("never says isn't available or Not available in this release on the agent and metadata options", () => {
    mount();
    const menu = openGroupBy();

    for (const name of ["Agent", ...KEYS]) {
      const option = within(menu).getByRole("menuitemradio", { name });
      expect(option.textContent).not.toMatch(NOT_THESE_WORDS);
      expect(option).not.toHaveAccessibleDescription(NOT_THESE_WORDS);
    }
  });
});
