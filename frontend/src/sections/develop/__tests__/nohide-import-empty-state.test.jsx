// Render the real first-run branch with the real launch claims and copy.
// Only data hooks and unrelated table/dialog infrastructure are stubbed.
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { palette } from "src/theme/palette";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { createDoorShown, offeredImportOptions } from "../importOptions";
import DevelopView from "../DevelopView";

const h = vi.hoisted(() => ({
  noop: () => {},
  Null: () => null,
  request: vi.fn(),
  data: { items: [], total: 0 },
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
vi.mock("src/components/data-table", () => ({
  DataTable: h.Null,
  DataTablePagination: h.Null,
}));
vi.mock("../hooks/useDatasetListQuery", () => ({
  useDatasetListQuery: () => ({
    data: h.data,
    isLoading: false,
    isError: false,
    refetch: h.noop,
  }),
  queryErrorSentence: () => null,
}));
vi.mock("../hooks/useDatasetStars", () => ({ default: h.noop }));
vi.mock("../hooks/useGroupCollapse", () => ({
  default: () => [new Set(), h.noop],
}));
vi.mock("../organize/useDatasetOrganize", () => ({
  default: () => ({ openFor: h.noop }),
}));
vi.mock("../organize/useFolderActions", () => ({ default: () => ({}) }));
vi.mock("../organize/realFolderApi", () => ({
  useRealFolderInventory: () => ({}),
}));
vi.mock("src/api/develop/dataset-org", () => ({
  useDatasetStar: () => ({ mutateAsync: h.request }),
  errorSentence: () => "Unexpected error",
}));
vi.mock("src/sections/common/ux/oneview/useColumnLayout", () => ({
  default: () => ({ visibleIds: [] }),
}));
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({
  ViewMenu: h.Null,
}));
vi.mock("src/sections/common/ux/useShortcuts", () => ({
  default: h.noop,
  openShortcutSheet: h.noop,
}));
vi.mock("src/sections/common/ux/KeyHintTooltip", async () => {
  const { default: PropTypes } = await import("prop-types");
  function TooltipShell({ children }) {
    return <>{children}</>;
  }
  TooltipShell.propTypes = { children: PropTypes.node };
  return { default: TooltipShell };
});
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
vi.mock("../AddDatasetDrawer/AddDatasetDrawer", () => ({ default: h.Null }));
vi.mock("../DuplicateDataset/DuplicateDataset", () => ({ default: h.Null }));
vi.mock("../organize/DatasetSettingsDialog", () => ({ default: h.Null }));
vi.mock("../DatasetListCells", () => ({ RowMenuList: h.Null }));
vi.mock("../datasetListColumns", () => ({ datasetListColumns: () => [] }));
vi.mock("../ListSelectionBar", () => ({ default: h.Null }));
vi.mock("../organize/DatasetListControls", () => ({
  DatasetFilterChips: h.Null,
  DatasetListControls: h.Null,
}));
vi.mock("../organize/FolderRail", () => ({ default: h.Null }));
vi.mock("../organize/FolderBreadcrumb", () => ({ default: h.Null }));
vi.mock("../organize/FolderDnd", () => ({ FolderDndProvider: h.Null }));
vi.mock("../organize/orgColumns", () => ({ datasetOrgColumns: () => [] }));

const theme = createTheme({ palette: palette("light") });
let client;
beforeEach(() => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  const noNetwork = () => {
    throw new Error("Network is forbidden in this test");
  };
  h.request.mockReset().mockImplementation(noNetwork);
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("names neither hidden production traces nor disabled Hugging Face in the launch empty state", () => {
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={["/dashboard/develop"]}>
          <DevelopView />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  expect(screen.getByText("Add your first dataset")).toBeVisible();
  expect(screen.getByRole("button", { name: "New dataset" })).toBeEnabled();
  expect(createDoorShown("manuallyCreateDataset")).toBe(true);
  expect(createDoorShown("fromProduction")).toBe(false);
  expect(
    offeredImportOptions().find(({ id }) => id === "importFromHuggingface"),
  ).toEqual(expect.objectContaining({ disabled: true }));
  expect(screen.getByText(/A dataset holds the tasks/)).not.toHaveTextContent(
    /production traces|Hugging Face/i,
  );
  expect(screen.getByText(/A dataset holds the tasks/)).toHaveTextContent(
    "A dataset holds the tasks you test your prompt or agent on. Upload a file or start with a blank grid.",
  );
  expect(h.request).not.toHaveBeenCalled();
});
