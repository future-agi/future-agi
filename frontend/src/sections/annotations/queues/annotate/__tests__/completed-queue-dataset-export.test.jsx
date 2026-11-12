import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  gridWritesClassic,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";
import AddDataSheet from "src/sections/common/AddData/AddDataSheet";
import {
  closeAddData,
  useAddDataStore,
} from "src/sections/common/AddData/addDataStore";
import AnnotateWorkspaceView from "../annotate-workspace-view";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snackbar: vi.fn(),
  user: { id: "annotator-1" },
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: mocks.get, post: mocks.post },
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: mocks.snackbar,
  closeSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: mocks.snackbar }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: mocks.user, role: "workspace_member" }),
}));
vi.mock("src/hooks/use-socket", () => ({ useSocket: () => ({}) }));
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: () => {} }));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [] }),
  useGetDatasetColumns: () => ({ data: [] }),
}));

// Leave the workspace, global AddDataSheet/ProductionDataSheet, queue export
// drawer, queue API hooks and workspace keyboard handling real. Only unrelated
// item editors and prototype-only sheet controls are replaced.
/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../annotate-header", () => ({ default: () => null }));
vi.mock("../annotate-footer", () => ({ default: () => null }));
vi.mock("../content-panel", () => ({ default: () => null }));
vi.mock("../label-panel", () => ({ default: () => null }));
vi.mock("../resizable-split", () => ({ default: () => null }));
vi.mock("../annotation-comparison-panel", () => ({ default: () => null }));
vi.mock("../discussion-panel", () => ({ CollaborationDrawer: () => null }));
vi.mock("../item-assignment-panel", () => ({ default: () => null }));
vi.mock("../ReviewSummaryDrawer", () => ({ default: () => null }));
vi.mock("src/sections/common/RowPanel/ShortcutSheet", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/ExperimentReviewSummary", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop/AddRowDrawer/UploadFileModal", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop/AddDatasetDrawer/UploadFileModal", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/RunSources/RunOnRowsButton", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/AddData/SourcePane", () => ({ default: () => null }));
vi.mock("src/sections/common/AddData/ShapePicker", () => ({ default: () => null }));
vi.mock("src/sections/common/AddData/MappingPane", () => ({
  default: () => null,
  IncludeFlags: () => null,
}));
vi.mock("src/sections/common/AddData/PreviewPane", () => ({
  default: () => null,
  TargetVersionLine: () => null,
}));
vi.mock("src/sections/common/AddData/SelectedItemPreview", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/AddData/ExpectedSourceControl", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/AddData/SplitTargetControl", () => ({
  default: () => null,
  newSplitProblem: () => null,
}));
vi.mock("src/sections/common/AddData/SourceFilterChips", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/AddData/KeepAsSource", () => ({ default: () => null }));
/* eslint-enable react/prop-types */

const QUEUE_ID = "queue-Q";
const QUEUE_API = `/model-hub/annotation-queues/${QUEUE_ID}/`;
const WORKSPACE_URL =
  `/dashboard/annotations/queues/${QUEUE_ID}/annotate?mode=annotate&from=queue`;
const FIELD_DEFINITIONS = [
  { id: "source_type", label: "Source type", column: "source_type" },
  { id: "input", label: "Input", column: "input" },
  { id: "output", label: "Output", column: "output" },
  {
    id: "label:quality:slot:1:value",
    label: "Quality annotation 1 score",
    column: "quality_score",
  },
  {
    id: "annotation_metrics",
    label: "Annotation metrics",
    column: "annotation_metrics",
  },
];
const COLUMN_MAPPING = FIELD_DEFINITIONS.map(({ id, column }) => ({
  field: id,
  column,
  enabled: true,
}));
const response = (result) => ({ data: { result } });
let client;
let observeProjects;

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

beforeEach(() => {
  vi.clearAllMocks();
  closeAddData();
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  setRealBackendForTests(true);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  observeProjects = [{ id: "unrelated-project", name: "Unrelated Observe project" }];
  mocks.get.mockImplementation(async (url) => {
    if (url === QUEUE_API) {
      return response({
        id: QUEUE_ID,
        name: "Completed corrections",
        status: "active",
        viewer_role: "annotator",
        viewer_roles: ["annotator"],
        annotators: [{ user_id: mocks.user.id, role: "annotator" }],
      });
    }
    if (url === `${QUEUE_API}progress/`) {
      return response({
        total: 2,
        completed: 2,
        skipped: 0,
        user_progress: { total: 2, completed: 2, skipped: 0 },
      });
    }
    if (url === `${QUEUE_API}items/next-item/`) return response({ item: null });
    if (url === `${QUEUE_API}export-fields/`) {
      return response({ fields: FIELD_DEFINITIONS, default_mapping: COLUMN_MAPPING });
    }
    if (url === endpoints.project.listProjects()) {
      return response({ projects: observeProjects });
    }
    throw new Error(`Unexpected GET ${url}`);
  });
  mocks.post.mockImplementation(async (url) => {
    if (url === `${QUEUE_API}export-to-dataset/`) {
      return response({ dataset_id: "exported", dataset_name: "Corrected rows", rows_created: 2 });
    }
    if (url === "/tracer/dataset/preview_sources/") return response({ items: [] });
    throw new Error(`Unexpected POST ${url}`);
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  closeAddData();
  setRealBackendForTests(null);
  setLaunchOverrideForTest(null);
});

async function openCompletedQueueExport() {
  expect(gridWritesClassic()).toBe(true);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[WORKSPACE_URL]}>
        <Routes>
          <Route
            path="/dashboard/annotations/queues/:queueId/annotate"
            element={<AnnotateWorkspaceView />}
          />
        </Routes>
        <AddDataSheet />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("All done")).toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Add corrected items to a dataset" }));
  // RED on both pinned bases: the hosted global sheet opens From production
  // instead, so the real queue export control never appears.
  expect(await screen.findByTestId("export-to-dataset-drawer")).toBeVisible();
  await waitFor(() => expect(mocks.get).toHaveBeenCalledWith(`${QUEUE_API}export-fields/`));
  expect(screen.getByRole("combobox", { name: /items to export/i })).toHaveTextContent("Completed only");
  await waitFor(() => expect(screen.getAllByTestId("export-mapping-row")).toHaveLength(COLUMN_MAPPING.length));
  return user;
}

function expectNoObserveImport() {
  expect(useAddDataStore.getState().open).toBe(false);
  expect(screen.queryByText("From production")).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "Project" })).not.toBeInTheDocument();
  expect(mocks.get.mock.calls.some(([url]) => url === endpoints.project.listProjects())).toBe(false);
  expect(mocks.post.mock.calls.some(([url]) => url.includes("/tracer/dataset/"))).toBe(false);
}

describe("Completed queue dataset export in the hosted profile", () => {
  it.each(["available", "absent"])(
    "exports queue source and label mappings with Observe projects %s",
    async (projects) => {
      if (projects === "absent") observeProjects = [];
      const user = await openCompletedQueueExport();
      expectNoObserveImport();
      await user.type(screen.getByLabelText(/Dataset name/), "Corrected rows");
      await user.click(screen.getByRole("button", { name: "Export" }));
      await waitFor(() => expect(mocks.post).toHaveBeenCalledWith(
        `${QUEUE_API}export-to-dataset/`,
        {
          dataset_name: "Corrected rows",
          status_filter: "completed",
          column_mapping: COLUMN_MAPPING,
        },
      ));
      expect(mocks.post).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(screen.queryByTestId("export-to-dataset-drawer")).not.toBeInTheDocument());
      expect(screen.getByText("All done")).toBeVisible();
      expect(screen.getByTestId("location")).toHaveTextContent(WORKSPACE_URL);
      expectNoObserveImport();
    },
  );

  it.each(["close button", "Escape"])(
    "returns to All done after %s and reopens with completed-only defaults",
    async (dismissal) => {
      const user = await openCompletedQueueExport();
      await user.click(screen.getByRole("combobox", { name: /items to export/i }));
      await user.click(screen.getByRole("option", { name: "All items" }));
      if (dismissal === "Escape") {
        await user.keyboard("{Escape}");
      } else {
        await user.click(screen.getByRole("button", { name: "Close export drawer" }));
      }
      await waitFor(() => expect(screen.queryByTestId("export-to-dataset-drawer")).not.toBeInTheDocument());
      expect(screen.getByText("All done")).toBeVisible();
      expect(screen.getByTestId("location")).toHaveTextContent(WORKSPACE_URL);
      expect(mocks.post).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "Add corrected items to a dataset" }));
      expect(await screen.findByTestId("export-to-dataset-drawer")).toBeVisible();
      expect(screen.getByRole("combobox", { name: /items to export/i })).toHaveTextContent("Completed only");
      expectNoObserveImport();
    },
  );
});
