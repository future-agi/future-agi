import { useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "src/utils/test-utils";
import { evalPresentationTheme } from "src/sections/evals/components/__tests__/evalPresentationTheme";
import AgentDefinitions from "src/pages/dashboard/agent-definitions/AgentDefinitions";
import AgentListView from "src/sections/agent-playground/AgentListView/AgentListView";
import TaskListView from "src/sections/common/EvalsTasks/TaskListView";
import PersonaListView from "src/sections/persona/PersonaListView";
import ExperimentTab from "src/sections/develop-detail/ExperimentTabClassic/ExperimentTab";
import ExperimentTabHeader from "src/sections/develop-detail/ExperimentTabClassic/ExperimentTabHeader";
import ProjectWrapperView from "src/sections/project/ProjectWrapperView";
import AlertsListView from "src/sections/projects/Alerts/components/AlertsListView/AlertsListView";
import ConfirmAlertsDialog from "src/sections/projects/Alerts/components/ConfirmAlertsDialog";
import { useAlertStore } from "src/sections/projects/Alerts/store/useAlertStore";
import TestRunsGrid from "src/sections/test/TestRuns/TestRunsGrid";
import TestRunsSelection from "src/sections/test/TestRuns/TestRunsSelection";
import { useTestRunsGridStore } from "src/sections/test/states";

const h = vi.hoisted(() => ({
  data: null, filters: [], path: "/dashboard/observe", refetch: vi.fn(),
  get: vi.fn(), post: vi.fn(), delete: vi.fn(), graphDelete: vi.fn(), personaDelete: vi.fn(),
}));
// Only query delivery is substituted: same-render cached/refetched data without a
// loading gap. Actual screens, shared table adapters and destructive handlers run.
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original()),
  useQuery: () => ({ data: h.data, isLoading: false, isPending: false, refetch: h.refetch }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: h.post, delete: h.delete },
  endpoints: {
    agentDefinitions: { list: "/agents", delete: "/agents/delete" },
    agentPlayground: { listGraphs: "/graphs" },
    develop: { deleteDataset: () => "/datasets/delete", experiment: {
      experimentListPaginated: "/experiments", rerun: "/experiments/rerun", delete: "/experiments/delete",
    } },
    project: { getEvalTaskList: () => "/tasks", getEvalTasksWithProjectName: () => "/tasks",
      markEvalsDeleted: () => "/tasks/delete", pauseEvalTask: () => "/pause", resumeEvalTask: () => "/resume",
      projectExperimentList: "/projects", deleteObservePrototype: "/projects/delete",
      getMonitorList: () => "/alerts", createMonitor: "/alerts/delete", muteAlerts: "/alerts/mute" },
    runTests: { detailExecutions: () => "/runs", deleteSimulation: () => "/runs/delete",
      rerunSimulation: () => "/runs/rerun" },
  },
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "Owner" }) }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), handleOnDocsClicked: vi.fn(), Events: {}, PropertyName: {} }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn(), useSnackbar: () => ({ enqueueSnackbar: vi.fn() }) }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("react-router", async (original) => ({
  ...(await original()), useParams: () => ({ dataset: "dataset-a", testId: "test-a" }),
  useLocation: () => ({ pathname: h.path }),
}));
vi.mock("src/sections/agent-playground/store", () => ({ useAgentPlaygroundStoreShallow: () => ({ setCurrentAgent: vi.fn() }) }));
vi.mock("src/sections/agent-playground/hooks/useCanEditAgent", () => ({ default: () => ({ canCreate: true, canDelete: true }) }));
vi.mock("src/api/agent-playground/agent-playground", () => ({
  useCreateGraph: () => ({ mutate: vi.fn() }),
  useDeleteGraphs: () => ({ mutate: h.graphDelete, isPending: false }),
}));
vi.mock("src/api/persona/persona", () => ({
  useGetPersonasPaginated: () => ({ data: h.data, isLoading: false }),
  useBulkDeletePersonas: () => ({ mutateAsync: h.personaDelete, isPending: false }),
}));
vi.mock("src/sections/develop-detail/states", () => ({ useRunExperimentStoreShallow: () => ({ initiateCreateMode: vi.fn() }) }));
vi.mock("src/sections/develop-detail/Experiment/api", () => ({ useReRunExperiment: vi.fn(), useStopExperiment: vi.fn() }));
vi.mock("src/sections/projects/UsersView/common", () => ({ formatNumberWithCommas: String }));
vi.mock("src/sections/projects/Alerts/store/useAlertFilterStore", () => ({ useAlertFilterShallow: () => ({ activeFilters: h.filters }) }));
vi.mock("src/sections/common/simulation", () => ({ EXECUTION_STATUS_ROLE: {}, STOPPABLE_STATUSES: [], TERMINAL_STATUSES: ["completed"], useCancelExecution: () => ({ mutate: vi.fn() }) }));
vi.mock("src/components/run-tests/common", () => ({ SIMULATION_TYPE: {} }));
vi.mock("src/sections/test/context/TestDetailContext", () => ({ useTestDetailContext: () => ({ refreshTestRunGrid: vi.fn() }) }));
vi.mock("src/sections/test/TestRuns/states", () => ({ useTestRunsSearchStoreShallow: (select) => select({ search: "" }) }));
vi.mock("src/sections/test/states", async () => {
  const { create } = await import("zustand");
  const store = create((set) => ({ toggledNodes: [], selectAll: false,
    reset: () => set({ toggledNodes: [], selectAll: false }),
    setToggledNodes: (ids) => set({ toggledNodes: ids }), setSelectAll: (all) => set({ selectAll: all }),
  }));
  return { useTestRunsGridStore: store, useTestRunsGridStoreShallow: (select) => select(store()),
    useTestEvaluationStoreShallow: () => ({ setOpenTestEvaluation: vi.fn() }) };
});
vi.mock("src/sections/test/common", async () => {
  const { useTestRunsGridStore: store } = await import("src/sections/test/states");
  return { useTestRunsSelectedCount: () => store((s) => s.toggledNodes.length) };
});

/* eslint-disable react/prop-types */
// Mock only MUI's visual grid; DataTable itself must implement the ID contract,
// resets and reconciliation. Checkbox events report IDs just as MUI X does.
vi.mock("@mui/x-data-grid", () => ({ DataGrid: ({ rows, getRowId, rowSelectionModel, onRowSelectionModelChange }) => (
  <section aria-label="Rows">{rows.map((row) => <label key={getRowId(row)}>
    <input type="checkbox" checked={rowSelectionModel.includes(getRowId(row))}
      onChange={(event) => onRowSelectionModelChange(event.target.checked
        ? [...rowSelectionModel, getRowId(row)] : rowSelectionModel.filter((id) => id !== getRowId(row)))} />
    {row.name || row.agent_name}
  </label>)}</section>
) }));
vi.mock("src/components/data-table", async () => ({
  DataTable: (await import("src/components/data-table/DataTable")).default,
  DataTablePagination: (await import("src/components/data-table/DataTablePagination")).default,
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/sections/project/VolumeBarChart", () => ({ default: () => null }));
vi.mock("src/sections/project/TagEditor", () => ({ default: () => null }));
vi.mock("src/sections/project/RightSection/ProjectRightSection", () => ({ default: () => null }));
vi.mock("src/sections/project/ProjectFtux", () => ({ default: () => null }));
vi.mock("src/sections/project/context/ProjectObserveContextProvider", () => ({ default: ({ children }) => children }));
vi.mock("src/sections/project/context/ProjectExperimentContextProvider", () => ({ default: ({ children }) => children }));
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({ default: () => null }));
vi.mock("src/components/filter-panel/FilterPanel", () => ({ default: () => null }));
vi.mock("src/sections/develop/AddDatasetDrawer/AddDatasetDrawer", () => ({ default: () => null }));
vi.mock("src/sections/develop/DuplicateDataset/DuplicateDataset", () => ({ default: () => null }));
vi.mock("src/sections/develop/DerivedDatasetsDrawer/DerivedDatasetsDrawer", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/BaseColumnsDrawer", () => ({ default: () => null }));
vi.mock("src/sections/persona/PersonaCreateEdit/PersonaCreateEditDrawer", () => ({ default: () => null }));
vi.mock("src/sections/persona/PersonaCreateEdit/DuplicatePersonas", () => ({ default: () => null }));
vi.mock("src/sections/persona/PersonaInfo/PersonaInfoDrawer", () => ({ default: () => null }));
vi.mock("src/sections/projects/Alerts/components/AlertsSheetView/AlertsSheetView", () => ({ default: () => null }));
vi.mock("src/sections/projects/Alerts/components/AlertsListView/AlertCells", () => ({
  IssueCell: () => null, LastTriggeredCell: () => null, StatusCell: () => null, TrendChartCell: () => null,
}));
vi.mock("src/sections/common/EvalsTasks/Renderers/FilterChipsRenderer", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalsTasks/Renderers/EvalsAndTasksCustomToolTip", () => ({ default: () => null }));
vi.mock("src/sections/projects/Alerts/components/AlertsListView/Actions", async () => {
  const { useAlertStore: store } = await import("src/sections/projects/Alerts/store/useAlertStore");
  return { default: () => {
    const { selectedRows, handleOpenActionModal } = store();
    return selectedRows.length ? <div>
      <button onClick={() => handleOpenActionModal("delete")}>Delete</button>
      <button onClick={() => handleOpenActionModal("mute")}>Mute</button>
      <button onClick={() => handleOpenActionModal("unmute")}>Unmute</button>
    </div> : null;
  } };
});
vi.mock("src/sections/develop-detail/Common/CustomDialog/CustomDialog", () => ({
  default: ({ open, onClickAction, actionButton, children }) => open
    ? <div role="dialog">{children}<button onClick={onClickAction}>{actionButton}</button></div> : null,
}));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: ({ open, onSubmit, actionBtnTitle }) => open
    ? <div role="dialog"><button onClick={onSubmit}>{actionBtnTitle}</button></div> : null,
}));
/* eslint-enable react/prop-types */

const row = (id) => ({ id, name: id, agent_name: id, status: "completed", isDefault: false,
  created_at: "2026-01-01T00:00:00Z", evals_applied: [], filters_applied: {} });
const A = row("record-a");
const B = row("unticked-b");
const C = row("record-c");
const D = row("unticked-d");
const setRows = (rows) => { h.data = { results: rows, graphs: rows, items: rows, table: rows,
  projects: rows, rows, result: { projects: rows }, count: 50, total: 50,
  totalRows: 50, total_count: 50, metadata: { total_rows: 50, total_count: 50 } }; };

function Experiments() {
  const ref = useRef(null);
  const [rowSelected, setRowSelected] = useState([]);
  const [experimentSearch, setExperimentSearch] = useState("");
  return <>
    <ExperimentTabHeader tabRef={ref} rowSelected={rowSelected} setRowSelected={setRowSelected}
      experimentSearch={experimentSearch} setExperimentSearch={setExperimentSearch} setCurrentTab={vi.fn()} />
    <ExperimentTab ref={ref} setRowSelected={setRowSelected} experimentSearch={experimentSearch} />
  </>;
}
function Alerts() { return <><AlertsListView /><ConfirmAlertsDialog /></>; }
function TestRuns() { return <><TestRunsGrid /><TestRunsSelection /></>; }

let client;
beforeEach(() => {
  vi.clearAllMocks();
  h.path = "/dashboard/observe";
  setRows([A, B, C]);
  h.get.mockResolvedValue({ data: {} });
  h.post.mockResolvedValue({ data: {} });
  h.delete.mockResolvedValue({ data: {} });
  h.personaDelete.mockResolvedValue({ deleted: 2, failed: [] });
  useAlertStore.setState({ selectedRows: [], selectedAll: false, actionModal: { state: false, type: null },
    mainPage: true, searchQuery: "", columns: [] });
  useTestRunsGridStore.getState().reset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});
afterEach(() => { cleanup(); client.clear(); });

const httpIds = (method, url, key) => () => h[method].mock.calls
  .filter(([endpoint]) => endpoint === url).map(([, body]) => (method === "delete" ? body.data : body)[key]);
const cases = [
  { id: "AD-REORDER", Component: AgentDefinitions, payloads: httpIds("delete", "/agents/delete", "agent_ids") },
  { id: "AG-REORDER", Component: AgentListView, payloads: () => h.graphDelete.mock.calls.map(([body]) => body.ids) },
  { id: "TASK-REORDER", Component: TaskListView, payloads: httpIds("post", "/tasks/delete", "eval_task_ids") },
  { id: "PERSONA-REORDER", Component: PersonaListView, payloads: () => h.personaDelete.mock.calls.map(([ids]) => ids) },
  { id: "EXP-RERUN-REORDER", Component: Experiments, action: "Re-Run", payloads: httpIds("post", "/experiments/rerun", "experiment_ids") },
  { id: "PROJECT-EXP-REORDER", Component: ProjectWrapperView, path: "/dashboard/experiment", payloads: httpIds("delete", "/projects/delete", "project_ids") },
  { id: "PROJECT-OBS-REORDER", Component: ProjectWrapperView, clearOnReorder: true, payloads: httpIds("delete", "/projects/delete", "project_ids") },
  { id: "ALERT-DELETE-REORDER", Component: Alerts, confirm: "Delete Alerts", payloads: httpIds("delete", "/alerts/delete", "ids") },
  { id: "ALERT-MUTE-REORDER", Component: Alerts, action: "Mute", confirm: "Mute Alerts", payloads: httpIds("post", "/alerts/mute", "ids") },
  { id: "ALERT-UNMUTE-REORDER", Component: Alerts, action: "Unmute", confirm: "Unmute Alerts", payloads: httpIds("post", "/alerts/mute", "ids") },
  { id: "RUN-REORDER", Component: TestRuns, action: "Delete (2)", confirm: "Delete", payloads: httpIds("post", "/runs/delete", "test_execution_ids") },
];

function mount(Component) {
  const ui = () => <QueryClientProvider client={client}><Component /></QueryClientProvider>;
  const view = render(ui(), { theme: evalPresentationTheme("light") });
  return (rows) => { setRows(rows); view.rerender(ui()); };
}
const tick = (id) => fireEvent.click(screen.getByLabelText(id));

describe("selectable screens use stable record identities", () => {
  it.each(cases)("$id sends only ticked IDs after polling reorder and freezes the confirmation", async ({ Component, path, action = "Delete", confirm, payloads, clearOnReorder }) => {
    h.path = path || h.path;
    const refresh = mount(Component);
    tick(A.id);
    refresh([B, A, C]);
    if (clearOnReorder) {
      expect(screen.getByLabelText(A.id)).not.toBeChecked();
      expect(screen.getByLabelText(B.id)).not.toBeChecked();
      tick(A.id);
    }
    expect(screen.getByLabelText(A.id)).toBeChecked();
    expect(screen.getByLabelText(B.id)).not.toBeChecked();
    tick(C.id); // Previously this extra checkbox included the unticked B.
    fireEvent.click(screen.getByRole("button", { name: action, exact: true }));
    const dialog = screen.getByRole("dialog");
    refresh([D, C, B, A]);
    // A change in the live selection while the dialog is open must not change
    // its targets or silently add an unticked experiment/monitor/project.
    tick(C.id);
    fireEvent.click(within(dialog).getByRole("button", { name: confirm || action, exact: true }));
    await waitFor(() => expect(payloads()).toHaveLength(1));
    expect([...payloads()[0]].sort()).toEqual([A.id, C.id].sort());
    expect(payloads()[0]).not.toContain(B.id);
    expect(payloads()[0]).not.toContain(D.id);
  });

  it.each(cases.filter((test) => !test.id.includes("MUTE")))(
    "$id clears cached page changes even when the same IDs remain visible", ({ Component, path }) => {
      h.path = path || h.path;
      mount(Component);
      tick(A.id);
      fireEvent.click(screen.getByRole("button", { name: "Next page" }));
      expect(screen.getByLabelText(A.id)).not.toBeChecked();
      expect(screen.getByLabelText(B.id)).not.toBeChecked();
    },
  );

  it("EXP-SEARCH clears before the debounce or replacement response", () => {
    mount(Experiments);
    tick(A.id);
    fireEvent.change(screen.getByPlaceholderText("Search"), { target: { value: "new scope" } });
    expect(screen.getByLabelText(A.id)).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "Re-Run", exact: true })).not.toBeInTheDocument();
    expect(h.post).not.toHaveBeenCalled();
  });

  it("ALERT-SCOPE clears the shared store on project scope changes", () => {
    const refresh = mount(Alerts);
    tick(A.id);
    act(() => useAlertStore.setState({ mainPage: false, selectedProject: "another-project" }));
    refresh([B, A, C]);
    expect(useAlertStore.getState().selectedRows).toEqual([]);
    expect(useAlertStore.getState().selectedAll).toBe(false);
  });

  it("PERSONA-PICKER keeps externally selected IDs across pages without shifting them", () => {
    function Picker() {
      const [selected, setSelected] = useState([A]);
      return <PersonaListView isSelectable selectedPersonas={selected} onToggleSelect={(item, checked) =>
        setSelected((previous) => checked ? [...previous, item] : previous.filter((p) => p.id !== item.id))} />;
    }
    const refresh = mount(Picker);
    expect(screen.getByLabelText(A.id)).toBeChecked();
    refresh([B, C]);
    expect(screen.getByLabelText(B.id)).not.toBeChecked();
    refresh([B, A, C]);
    expect(screen.getByLabelText(A.id)).toBeChecked();
    expect(h.personaDelete).not.toHaveBeenCalled();
  });
});
