// W49: the toolbar can render before the grid's page-0 read completes.
// Keep the real query observer, action builder, ActionBar and More menu. A
// cache write models the grid completing its read while More stays open;
// no first-experiment empty state warms the cache for this fixture.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { getDatasetQueryKey } from "src/api/develop/develop-detail";
import { actionsFor } from "src/sections/common/ux/oneview/ActionBar";
import {
  useAddColumnDrawerStore,
  useDatasetOriginStore,
  useDevelopSelectedRowsStore,
  useRunEvaluationStore,
} from "src/sections/develop-detail/states";
import DevelopDataRightSection from "../DevelopDataRightSection";

const fixture = vi.hoisted(() => ({
  exportDialog: vi.fn(),
  schemaDrawer: vi.fn(),
  noop: () => {},
  Null: () => null,
}));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: fixture.noop,
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: fixture.Null,
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    isSurfaceShown: (id) => id === "dataset.schema" || actual.isSurfaceShown(id),
  };
});
vi.mock("src/sections/common/Access/useDatasetAccess", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({
    can: { edit: true, run: true, download: true },
    reason: () => "",
  }),
}));
vi.mock("src/sections/common/ux/oneview/ViewMenu", async (importOriginal) => ({
  ...(await importOriginal()),
  default: fixture.Null,
}));
vi.mock(
  "src/sections/common/CommandPalette/commandRegistry",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRegisterCommands: fixture.noop,
  }),
);
vi.mock("src/sections/common/export/ExportDialog", async (importOriginal) => ({
  ...(await importOriginal()),
  DatasetExportDialog: (props) => {
    fixture.exportDialog(props);
    return null;
  },
}));
vi.mock("src/sections/common/Values/SchemaDrawer", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (props) => {
    fixture.schemaDrawer(props);
    return null;
  },
}));
vi.mock("src/sections/develop/AddRowDrawer/AddRowDrawer", async (importOriginal) => ({
  ...(await importOriginal()),
  default: fixture.Null,
}));
vi.mock(
  "src/sections/develop/AddRowDrawer/EditSyntheticData/SyntheticSummaryDrawer",
  async (importOriginal) => ({ ...(await importOriginal()), default: fixture.Null }),
);
vi.mock("src/sections/develop-detail/AddColumn/AddColumnDrawer", async (importOriginal) => ({
  ...(await importOriginal()),
  default: fixture.Null,
}));
vi.mock(
  "src/sections/develop-detail/DevelopBarRightSection/DevelopDataSelectionActive",
  async (importOriginal) => ({ ...(await importOriginal()), default: fixture.Null }),
);
vi.mock(
  "src/sections/develop-detail/DataTab/DataGrid/useDataToolbarView",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => ({
      gridView: { density: "comfortable", setDensity: fixture.noop },
      viewState: {},
      applyView: fixture.noop,
      resetView: fixture.noop,
    }),
  }),
);
vi.mock(
  "src/sections/develop-detail/DataTab/DataGrid/useDataGridLayout",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => ({ isDefault: true }),
  }),
);
vi.mock(
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/useRowViewUrl",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => ({ openRow: fixture.noop }),
  }),
);

const DATASET = "w49-dataset";
const pageKey = getDatasetQueryKey(DATASET, 0, [], [], "");
const page = (count) => ({
  data: {
    result: {
      table: Array.from({ length: count }, (_, i) => ({ row_id: `row-${i + 1}` })),
      column_config: [],
      metadata: { total_rows: count, dataset_name: "Questions", version: { number: 1 } },
    },
  },
});
let client;

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  fixture.exportDialog.mockClear();
  fixture.schemaDrawer.mockClear();
  useRunEvaluationStore.setState({ openRunEvaluation: false });
  useAddColumnDrawerStore.setState({ openAddColumnDrawer: false });
  useDevelopSelectedRowsStore.setState({ selectAll: false, toggledNodes: [] });
  useDatasetOriginStore.setState({ processingComplete: true });
  const noNetwork = () => { throw new Error("Unexpected network in toolbar regression"); };
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
});

afterEach(() => {
  cleanup();
  client.clear();
  useRunEvaluationStore.setState({ openRunEvaluation: false });
  useAddColumnDrawerStore.setState({ openAddColumnDrawer: false });
  useDevelopSelectedRowsStore.setState({ selectAll: false, toggledNodes: [] });
  useDatasetOriginStore.setState({ processingComplete: false });
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mountToolbar() {
  renderWithRouter(
    <QueryClientProvider client={client}>
      <Routes>
        <Route path="/dashboard/develop/:dataset" element={<DevelopDataRightSection />} />
      </Routes>
    </QueryClientProvider>,
    { route: `/dashboard/develop/${DATASET}` },
  );
}
const openMore = () =>
  fireEvent.click(screen.getByRole("button", { name: "More actions" }));

it.each([
  ["Evaluate", "evaluate"],
  ["Add column", "add-column"],
  ["Schema", "schema"],
  ["Download…", "download"],
])("W49-ROWS-ARRIVE: %s follows the live four-row read without reopening More", async (label, id) => {
  mountToolbar();
  openMore();
  // More also lists "<label> guide" links; the action row is the other one.
  expect(screen.getByRole("menuitem", { name: new RegExp(`^${label}(?! guide$)`) })).toHaveAttribute(
    "aria-disabled",
    "true",
  );

  await act(async () => {
    client.setQueryData(pageKey, page(4));
  });
  // Prove that the toolbar already knows there are rows. The regression is
  // the open menu retaining the old disabled actions, not an empty dataset.
  await waitFor(() =>
    expect(actionsFor("grid").find((entry) => entry.id === id).disabled).toBe(false),
  );

  const item = screen.getByRole("menuitem", { name: label });
  expect(item).not.toHaveAttribute("aria-disabled", "true");
  expect(screen.queryByText("Add rows first")).toBeNull();
  expect(screen.queryByText("Nothing to export yet")).toBeNull();
  fireEvent.click(item);
  if (id === "evaluate") {
    expect(useRunEvaluationStore.getState().openRunEvaluation).toBe(true);
  } else if (id === "add-column") {
    expect(useAddColumnDrawerStore.getState().openAddColumnDrawer).toBe(true);
  } else if (id === "schema") {
    expect(fixture.schemaDrawer).toHaveBeenLastCalledWith(expect.objectContaining({ open: true }));
  } else {
    expect(fixture.exportDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({ open: true, datasetId: DATASET, datasetName: "Questions" }),
    );
  }
});

it("W49-EMPTY: a confirmed empty dataset still disables Evaluate and Download", () => {
  client.setQueryData(pageKey, page(0));
  mountToolbar();
  openMore();
  expect(screen.getByRole("menuitem", { name: /^Evaluate Add rows first$/ })).toHaveAttribute(
    "aria-disabled",
    "true",
  );
  expect(screen.getByRole("menuitem", { name: /^Download… Nothing to export yet$/ })).toHaveAttribute(
    "aria-disabled",
    "true",
  );
});
