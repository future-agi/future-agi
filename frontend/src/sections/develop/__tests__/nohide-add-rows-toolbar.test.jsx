// NOHIDE-IMPORT round 2: restoring the legacy HF tile also restores the
// toolbar door. It opens availability information, not promised import methods.
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { palette } from "src/theme/palette";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import DevelopDataRightSection from "src/sections/develop-detail/DevelopBarRightSection/DevelopDataRightSection";

const h = vi.hoisted(() => ({
  post: vi.fn(),
  read: vi.fn(),
  drawer: vi.fn(),
  canEdit: true,
  noop: () => {},
  Null: () => null,
}));
vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => h.post(...args) },
  endpoints: {},
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/components/iconify", () => ({ default: h.Null }));
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["nohide-toolbar", id],
    queryFn: h.read,
    enabled: false,
    initialData: {
      data: { result: { table: [{}], metadata: { version: { number: 1 } } } },
    },
  }),
}));
vi.mock("src/sections/common/ux/oneview/ActionBar", async () => {
  const { default: PropTypes } = await import("prop-types");
  // Only the responsive shell is stubbed. Render the real action elements,
  // including the actual Add rows button and CustomTooltip.
  function InlineActions({ actions }) {
    return (
      <>
        {actions
          .filter((action) => action.render)
          .map((action) => (
            <React.Fragment key={action.id}>{action.render()}</React.Fragment>
          ))}
      </>
    );
  }
  InlineActions.propTypes = {
    actions: PropTypes.arrayOf(PropTypes.object).isRequired,
  };
  return { default: InlineActions };
});
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({ default: h.Null }));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: h.noop,
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: h.noop }));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  DatasetExportDialog: h.Null,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { edit: h.canEdit, run: true, download: true },
    reason: () => "Fixture edit denied",
  }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows" }),
}));
vi.mock("src/sections/common/Values/SchemaDrawer", () => ({ default: h.Null }));
vi.mock("src/sections/develop/AddRowDrawer/AddRowDrawer", () => ({
  default: (props) => {
    h.drawer(props);
    return null;
  },
}));
vi.mock(
  "src/sections/develop/AddRowDrawer/EditSyntheticData/SyntheticSummaryDrawer",
  () => ({ default: h.Null }),
);
vi.mock("src/sections/develop/AddRowDrawer/EditSyntheticData/state", () => ({
  useEditSyntheticDataStore: () => ({ setOpenSummaryDrawer: h.noop }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: h.noop }),
}));
vi.mock(
  "src/sections/develop-detail/DataTab/DataGrid/useDataToolbarView",
  () => ({
    default: () => ({
      gridView: { density: "comfortable", setDensity: h.noop },
      viewState: {},
      applyView: h.noop,
      resetView: h.noop,
    }),
  }),
);
vi.mock(
  "src/sections/develop-detail/DataTab/DataGrid/useDataGridLayout",
  () => ({
    default: () => ({ isDefault: true }),
    DATA_GRID_SURFACE: "grid",
  }),
);
vi.mock("src/sections/develop-detail/DataTab/DataGrid/useDataGridView", () => ({
  getDataGridFilters: h.noop,
  getDataGridSearch: h.noop,
}));
vi.mock(
  "src/sections/develop-detail/DataTab/DataGrid/FloatingSelectionBar",
  () => ({ default: h.Null }),
);
vi.mock("src/sections/develop-detail/DataTab/Versions/changeSetStore", () => ({
  useChangeSetStore: {},
}));
vi.mock(
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/useRowViewUrl",
  () => ({ default: () => ({ openRow: h.noop }) }),
);
vi.mock("src/sections/develop-detail/AddColumn/AddColumnDrawer", () => ({
  default: h.Null,
}));
vi.mock(
  "src/sections/develop-detail/DevelopBarRightSection/DevelopDataSelectionActive",
  () => ({ default: h.Null }),
);
vi.mock("src/sections/develop-detail/TaskSuite/EnvironmentDrawer", () => ({
  default: h.Null,
}));
vi.mock("src/sections/develop-detail/states", () => ({
  useRunPromptStoreShallow: (selector) =>
    selector({ setOpenRunPrompt: h.noop }),
  useAddColumnDrawerStoreShallow: (selector) =>
    selector({ setOpenAddColumnDrawer: h.noop }),
  useRunEvaluationStoreShallow: (selector) =>
    selector({ setOpenRunEvaluation: h.noop }),
  useDevelopSelectedRowsStoreShallow: (selector) =>
    selector({ selectAll: false, toggledNodes: [] }),
  useDatasetOriginStore: () => ({ processingComplete: true }),
}));

const theme = createTheme({
  palette: palette("light"),
  components: { MuiTooltip: { defaultProps: { enterDelay: 0 } } },
});
let client;
beforeEach(() => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  h.canEdit = true;
  h.post.mockReset();
  h.read.mockReset();
  h.drawer.mockReset();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const noNetwork = () => {
    throw new Error("Network is forbidden in this test");
  };
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
});
afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function mountToolbar() {
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={["/dashboard/develop/d1"]}>
          <Routes>
            <Route
              path="/dashboard/develop/:dataset"
              element={<DevelopDataRightSection />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

it("offers availability, not absent import methods, under real launch claims", async () => {
  mountToolbar();
  const button = screen.getByRole("button", { name: "Add rows" });
  expect(button).toBeVisible();
  expect(button).toBeEnabled();
  fireEvent.mouseOver(button.parentElement);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "View row import options and availability.",
  );
  expect(screen.getByRole("tooltip")).not.toHaveTextContent(
    /production traces|a file|a paste|another dataset|blank row/,
  );
  fireEvent.click(button);
  expect(h.drawer).toHaveBeenLastCalledWith(
    expect.objectContaining({ open: true }),
  );
  expect(h.post).not.toHaveBeenCalled();
  expect(h.read).not.toHaveBeenCalled();
});

it("keeps the prototype tooltip unchanged", async () => {
  setLaunchOverrideForTest({ launch: false });
  mountToolbar();
  fireEvent.mouseOver(
    screen.getByRole("button", { name: "Add rows" }).parentElement,
  );
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "From production traces, a file, a paste, another dataset… or a blank row",
  );
});

it("preserves the dataset edit gate and its reason", async () => {
  h.canEdit = false;
  mountToolbar();
  const button = screen.getByRole("button", { name: "Add rows" });
  expect(button).toBeDisabled();
  fireEvent.mouseOver(button.parentElement);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "Fixture edit denied",
  );
  fireEvent.click(button);
  expect(h.drawer).toHaveBeenLastCalledWith(
    expect.objectContaining({ open: false }),
  );
  expect(h.post).not.toHaveBeenCalled();
});
