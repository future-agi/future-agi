import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils.jsx";
import { Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";

const { mocks, grid, Null, Passthrough } = vi.hoisted(() => ({
  mocks: {
    post: vi.fn(),
    delete: vi.fn(),
    enqueueSnackbar: vi.fn(),
    role: "Owner",
    selectorProps: null,
    summary: { vals: [] },
  },
  grid: {
    props: null,
    api: {
      refreshServerSide: vi.fn(),
      refreshCells: vi.fn(),
      showNoRowsOverlay: vi.fn(),
      deselectAll: vi.fn(),
    },
  },
  Null: () => null,
  Passthrough: ({ children }) => children,
}));

// Keep DevelopDetailView -> DevelopBar -> CompareDataRightSection, ColumnMenu,
// CompareData and the download hook real. Only AG Grid's layout boundary and
// unrelated tabs/drawers are replaced; a toolbar-only test missed this wiring.
vi.mock("ag-grid-react", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    AgGridReact: forwardRef(function Grid(props, ref) {
      grid.props = props;
      useImperativeHandle(ref, () => ({ api: grid.api }), []);
      return null;
    }),
  };
});
vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    dataset: {
      getCompareDataset: (id) => `/model-hub/datasets/${id}/compare-datasets/`,
      getCompareDatasetDownload: (id) =>
        `/model-hub/datasets/${id}/compare-datasets/download/`,
      deleteCompareDataset: (id) => `/model-hub/datasets/delete-compare/${id}/`,
    },
  },
}));
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mocks.enqueueSnackbar,
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: mocks.role }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "dataset-a", name: "Dataset A" }],
  }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => undefined }));
vi.mock("src/hooks/use-local-storage", () => ({
  useLocalStorage: () => ({ state: mocks.summary, update: vi.fn() }),
}));
vi.mock("src/utils/utils", () => ({
  preventHeaderSelection: vi.fn(),
  resetDLabels: vi.fn(),
}));
vi.mock("src/utils/MenuIconSet/setMeniIcons", () => ({
  setMenuIcons: vi.fn(() => []),
}));
vi.mock("./DataTab/CustomCompareColumnHeader", () => ({ default: Null }));
vi.mock("src/sections/common/DevelopCellRenderer/CustomCellRender", () => ({
  default: Null,
}));
vi.mock("src/sections/experiment-detail/ExperimentData/tableConfig", () => ({
  ExperimentDataDefaultColDef: {},
}));
vi.mock(
  "src/sections/experiment-detail/ExperimentData/CustomExperimentGroupHeader",
  () => ({ default: Null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentData/ExperimentStatusBar",
  () => ({ default: Null }),
);
vi.mock(
  "./DataTab/CompareDatasetDetailDrawer/CompareDatasetDetailDrawer",
  () => ({ default: Null }),
);
vi.mock(
  "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog",
  () => ({ default: Null }),
);
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({ AudioPlaybackProvider: Passthrough }),
);
vi.mock("./DatasetsSelectRow", () => ({
  default: (props) => {
    mocks.selectorProps = props;
    return <div>Selected dataset controls</div>;
  },
}));
vi.mock("./DevelopBarRightSection/DevelopBarLeftSection", () => ({
  default: () => <div>Dataset filters</div>,
}));
vi.mock("./DevelopBarRightSection/DevelopDataRightSection", () => ({
  default: () => <div>Dataset actions</div>,
}));
vi.mock("./DevelopBarRightSection/DevelopExperimentRightSection", () => ({
  default: () => <div>Retired experiment actions</div>,
}));
vi.mock("./DevelopBarRightSection/CompareSummaryRightSection", () => ({
  default: () => <div>Retired comparison summary actions</div>,
}));
vi.mock("./ExperimentTab/ExperimentTabHeader", () => ({
  default: () => <div>Experiment actions</div>,
}));
vi.mock("./DatasetSummaryTab/DatasetSummaryTab", () => ({
  default: () => <div>Dataset summary</div>,
}));
vi.mock("./DatasetSummaryTab/CompareSummaryTab", () => ({
  default: () => <div>Comparison summary</div>,
}));
vi.mock("./Evaluation/RunCompareEvaluation", () => {
  const Evaluation = ({ open }) =>
    open ? <div role="dialog" aria-label="Run comparison evaluation" /> : null;
  Evaluation.propTypes = { open: PropTypes.bool };
  return { default: Evaluation };
});
vi.mock("./RunPrompt/RunPrompt", () => ({ default: Null }));
vi.mock("./Optimization/RunOptimization", () => ({ default: Null }));
vi.mock("./OptimizationTab/OptimizeTab", () => ({ default: Null }));
vi.mock("./Evaluation/EditEvaluation", () => ({ default: Null }));
vi.mock("./ExperimentTab/ExperimentTab", () => ({ default: Null }));
vi.mock("./AddColumn/AddColumnApiCall/AddColumnApiCall", () => ({ default: Null }));
vi.mock("./AddColumn/ExtractEntities/ExtractEntities", () => ({ default: Null }));
vi.mock("./AddColumn/Classification/Classification", () => ({ default: Null }));
vi.mock("./AddColumn/ExecuteCode/ExecuteCode", () => ({ default: Null }));
vi.mock("./AddColumn/ExtractJsonKey/ExtractJsonKey", () => ({ default: Null }));
vi.mock("./AddColumn/Retrieval/Retrieval", () => ({ default: Null }));
vi.mock("./AddColumn/ConditionalNode/ConditionalNodeV2", () => ({ default: Null }));
vi.mock("./Experiment/RunExperiment", () => ({ default: Null }));
vi.mock("./DataTab/DevelopDataV2", () => ({ default: Null }));
vi.mock("./DataTab/DevelopEvaluationDrawer", () => ({ default: Null }));

import DevelopDetailView from "./DevelopDetailView";
import { COMPARISON_EXPIRED_MESSAGE } from "src/api/develop/dataset-compare";

const COMPARE_ID = "00000000-0000-4000-8000-000000000001";
const NEW_COMPARE_ID = "00000000-0000-4000-8000-000000000002";
const DOWNLOAD_URL = "/model-hub/datasets/dataset-a/compare-datasets/download/";
const TABLE_URL = "/model-hub/datasets/dataset-a/compare-datasets/";
const selectedDatasetData = [
  { value: "dataset-a", label: "Dataset A" },
  { value: "dataset-b", label: "Dataset B" },
];
const column = (id, name, groupId, groupName) => ({
  id,
  name,
  data_type: "text",
  origin_type: "others",
  status: "Completed",
  group: { id: groupId, name: groupName, data_type: "text", origin: "Dataset" },
  average_score: null,
});
const tableResponse = ({ empty = false, compareId = COMPARE_ID } = {}) => ({
  data: {
    status: true,
    result: {
      column_config: empty
        ? []
        : [
            column("input-a", "input", "input-a", "input"),
            column("output-a", "Dataset A", "output-a", "output"),
            column("output-b", "Dataset B", "output-a", "output"),
          ],
      table: empty
        ? []
        : [
            {
              row_id: "joined-1",
              "input-a": { cell_value: "question", cell_row_id: "a-1" },
              "output-a": { cell_value: "left", cell_row_id: "a-1" },
              "output-b": { cell_value: "right", cell_row_id: "b-1" },
            },
          ],
      metadata: {
        compare_id: compareId,
        total_rows: empty ? 0 : 1,
        total_pages: empty ? 0 : 1,
      },
    },
  },
});

const clients = [];
function renderView({ tab = "data", fromScenario = false, ...state } = {}) {
  window.history.replaceState(
    {
      usr: {
        isCompare: true,
        compareFromOutside: true,
        baseColumn: "input",
        selectedDatasets: ["dataset-a", "dataset-b"],
        selectedDatasetsValues: selectedDatasetData,
        ...state,
      },
      key: "compare-toolbar-test",
      idx: 0,
    },
    "",
    `/datasets/dataset-a?tab=${tab}${fromScenario ? "&fromScenario=true" : ""}`,
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <HelmetProvider>
        <Routes>
          <Route path="/datasets/:dataset" element={<DevelopDetailView />} />
        </Routes>
      </HelmetProvider>
    </QueryClientProvider>,
  );
}

async function loadPage() {
  const success = vi.fn();
  const fail = vi.fn();
  await act(async () => {
    await grid.props.serverSideDatasource.getRows({
      request: { startRow: 0, endRow: 10 },
      success,
      fail,
    });
  });
  expect(fail).not.toHaveBeenCalled();
  return success;
}

const downloadButton = () =>
  screen.getByRole("button", { name: "Download comparison CSV" });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.role = "Owner";
  mocks.selectorProps = null;
  grid.props = null;
  mocks.post.mockReset();
  mocks.post.mockImplementation((url) =>
    Promise.resolve(
      url === DOWNLOAD_URL
        ? { data: "input,output\nquestion,left\n" }
        : tableResponse(),
    ),
  );
  mocks.delete.mockResolvedValue({ data: { status: true } });
  window.URL.createObjectURL = vi.fn(() => "blob:comparison");
  window.URL.revokeObjectURL = vi.fn();
  vi.spyOn(window.HTMLAnchorElement.prototype, "click").mockImplementation(
    () => {},
  );
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
});

describe("DevelopDetailView comparison toolbar", () => {
  it("renders one toolbar on the actual comparison Data path and downloads its current comparison", async () => {
    const user = userEvent.setup();
    renderView();
    await loadPage();

    expect(screen.getAllByRole("button", { name: "Download comparison CSV" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Columns" })).toHaveLength(1);
    expect(screen.queryByText("Dataset actions")).not.toBeInTheDocument();
    expect(screen.getAllByText("Selected dataset controls")).toHaveLength(1);
    expect(mocks.selectorProps).toMatchObject({
      isCompareDataset: true,
      baseColumn: "input",
      selectedDatasetData,
    });

    await user.click(downloadButton());
    await waitFor(() => expect(window.URL.createObjectURL).toHaveBeenCalledTimes(1));
    expect(mocks.post.mock.calls.filter(([url]) => url === DOWNLOAD_URL)).toEqual([
      [DOWNLOAD_URL, {
        dataset_ids: ["dataset-b"],
        base_column_name: "input",
        compare_id: COMPARE_ID,
      }],
    ]);
    expect(window.HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    expect(window.URL.revokeObjectURL).toHaveBeenCalledWith("blob:comparison");
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
      "Dataset downloaded successfully",
      { variant: "success" },
    );
  });

  it("keeps the download button in place and prevents duplicate requests while downloading", async () => {
    const user = userEvent.setup();
    renderView();
    await loadPage();
    let resolveDownload;
    mocks.post.mockImplementationOnce(() => new Promise((resolve) => {
      resolveDownload = resolve;
    }));

    const button = downloadButton();
    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(within(button).getByRole("progressbar")).toBeInTheDocument();
    await user.click(button);
    expect(mocks.post.mock.calls.filter(([url]) => url === DOWNLOAD_URL)).toHaveLength(1);

    await act(async () => resolveDownload({ data: "input,output\n" }));
    await waitFor(() => expect(button).toBeEnabled());
    expect(downloadButton()).toBe(button);
    expect(button).toHaveAttribute("aria-busy", "false");
    expect(within(button).queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("opens the real column menu with the keyboard and applies column visibility to the comparison", async () => {
    const user = userEvent.setup();
    renderView();
    const columnsButton = screen.getByRole("button", { name: "Columns" });
    expect(columnsButton).toBeDisabled();
    await loadPage();
    expect(columnsButton).toBeEnabled();
    expect(grid.props.columnDefs.find((col) => col.field === "input-a").hide).toBe(false);

    columnsButton.focus();
    await user.keyboard("{Enter}");
    expect(columnsButton).toHaveAttribute("aria-expanded", "true");
    const inputLabel = screen.getByText("input").closest("label");
    const checkbox = within(inputLabel).getByRole("checkbox");
    expect(checkbox).toBeChecked();
    await user.click(checkbox);
    await waitFor(() => {
      expect(grid.props.columnDefs.find((col) => col.field === "input-a").hide).toBe(true);
    });
    expect(checkbox).not.toBeChecked();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(columnsButton).toHaveAttribute("aria-expanded", "false"));
  });

  it("keeps an empty comparison downloadable and wires typed-expiration recovery back to its grid", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce(tableResponse({ empty: true }));
    renderView();
    const success = await loadPage();
    expect(success).toHaveBeenCalledExactlyOnceWith({ rowData: [], rowCount: 0 });
    expect(grid.api.showNoRowsOverlay).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Columns" })).toBeDisabled();
    expect(downloadButton()).toBeEnabled();
    grid.api.refreshServerSide.mockClear();
    mocks.post.mockRejectedValueOnce({ statusCode: 404, code: "compare_not_found" });

    await user.click(downloadButton());
    await waitFor(() => expect(grid.api.refreshServerSide).toHaveBeenCalledExactlyOnceWith({ purge: true }));
    expect(mocks.post).toHaveBeenLastCalledWith(DOWNLOAD_URL, {
      dataset_ids: ["dataset-b"],
      base_column_name: "input",
      compare_id: COMPARE_ID,
    });
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(COMPARISON_EXPIRED_MESSAGE, { variant: "info" });
    expect(window.URL.createObjectURL).not.toHaveBeenCalled();

    // AG Grid's reload is manual at this layout boundary. The real datasource
    // must rebuild with null, rather than reusing its terminal empty snapshot.
    mocks.post.mockResolvedValueOnce(tableResponse({ compareId: NEW_COMPARE_ID }));
    await loadPage();
    expect(mocks.post).toHaveBeenLastCalledWith(TABLE_URL, expect.objectContaining({ compare_id: null }));
    await waitFor(() => expect(downloadButton()).toBeEnabled());
    await user.click(downloadButton());
    await waitFor(() => expect(window.URL.createObjectURL).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenLastCalledWith(DOWNLOAD_URL, {
      dataset_ids: ["dataset-b"],
      base_column_name: "input",
      compare_id: NEW_COMPARE_ID,
    });
  });

  it.each([
    { baseColumn: "" },
    { selectedDatasets: [] },
    { selectedDatasets: ["dataset-a"] },
  ])("disables download for an incomplete comparison selection %j", async (state) => {
    const user = userEvent.setup();
    renderView(state);
    expect(downloadButton()).toBeDisabled();
    await user.click(downloadButton());
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("runs the existing evaluation action for a loaded comparison", async () => {
    const user = userEvent.setup();
    renderView();
    expect(screen.getByRole("button", { name: "Evaluate" })).toBeDisabled();
    await loadPage();
    await user.click(screen.getByRole("button", { name: "Evaluate" }));
    expect(screen.getAllByRole("dialog", { name: "Run comparison evaluation" })).toHaveLength(1);
  });

  it.each([
    { role: "Viewer", fromScenario: false },
    { role: "Owner", fromScenario: true },
  ])("preserves evaluation restrictions for %j without blocking download", async ({ role, fromScenario }) => {
    mocks.role = role;
    renderView({ fromScenario });
    await loadPage();
    expect(screen.getByRole("button", { name: "Evaluate" })).toBeDisabled();
    expect(downloadButton()).toBeEnabled();
  });

  it.each([
    { isCompare: false, tab: "data", content: "Dataset actions" },
    { isCompare: false, tab: "experiments", content: "Experiment actions" },
    { isCompare: false, tab: "summary", content: "Dataset summary" },
    { isCompare: true, tab: "summary", content: "Comparison summary" },
  ])("preserves the dedicated toolbar/content without resurrecting retired slots for %j", ({ content, ...state }) => {
    renderView(state);
    expect(screen.getAllByText(content)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Download comparison CSV" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Columns" })).not.toBeInTheDocument();
    expect(screen.queryByText("Retired experiment actions")).not.toBeInTheDocument();
    expect(screen.queryByText("Retired comparison summary actions")).not.toBeInTheDocument();
    expect(screen.getAllByText("Selected dataset controls")).toHaveLength(1);
  });
});
