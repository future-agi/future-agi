// FR-EXPERIMENTS-03: the classic Experiments tab renders
// <ExperimentTabEmptyState sdkRunShown={sdkRunShown} /> with no
// onRunExperiment (ExperimentTabClassic/ExperimentTab.jsx). The first-run
// state must still carry the action its copy promises: the dataset's gated
// New experiment entry, opening the wizard through the pending-changes gate.
// r2: the action reads this dataset's own first page (live: mount, focus,
// invalidation, and polling while rows arrive), offers nothing where the page
// mounts no wizard, and a disabled action says why.
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { getDatasetQueryKey } from "src/api/develop/develop-detail";
import {
  useDatasetOriginStore,
  useRunExperimentStore,
} from "src/sections/develop-detail/states";
import { useChangeSetStore } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { useVersionDialogs } from "src/sections/develop-detail/DataTab/Versions/versionDialogsStore";
import ExperimentTabEmptyState from "../ExperimentTabEmptyState";

const fixture = vi.hoisted(() => ({
  dataset: "44444444-4444-4444-8444-444444444444",
  role: "Owner",
  canRun: true,
  runReason: null,
  classicShown: true,
  // The server's page-0 result for this dataset, and whether it answers.
  page: null,
  hold: false,
}));
const http = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: fixture.role }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { run: fixture.canRun },
    reason: () => fixture.runReason,
  }),
}));
// The classic creation host is shown; the NEW kinds menu stays off, so the
// entry is the single New experiment button the c2.6 launch line shows.
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    isSurfaceShown: (id) => {
      if (id === "exp.classic") return fixture.classicShown;
      if (id === "exp.new.kindMenu") return false;
      return actual.isSurfaceShown(id);
    },
  };
});
vi.mock(
  "src/sections/common/Datasets/datasetKind",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
  }),
);
// A workspace with more than 100 datasets: the list's first page does not
// hold this dataset. The action must not read "has rows" from that page.
vi.mock(
  "src/sections/develop/hooks/useDatasetsList",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDatasetsList: () => ({
      data: {
        items: Array.from({ length: 100 }, (_, i) => ({
          id: `other-${i}`,
          numberOfDatapoints: 5,
        })),
        total: 140,
      },
    }),
  }),
);
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
/* eslint-disable react/prop-types */
// Expose the exact tooltip title without jsdom hover timing.
vi.mock("src/components/tooltip", () => ({
  default: ({ children, title, show }) => (
    <div>
      {show ? <div data-testid="tooltip-copy">{title}</div> : null}
      {children}
    </div>
  ),
}));
/* eslint-enable react/prop-types */

const SDK_COPY =
  "The SDK run above is shown by its run id. This table lists the experiments run in the app; this dataset has none yet.";

const rowsPage = (count, extra = {}) => ({
  table: Array.from({ length: count }, (_, i) => ({ row_id: `row-${i + 1}` })),
  metadata: { total_rows: count },
  ...extra,
});
const generatingPage = (percentage) =>
  rowsPage(2, {
    synthetic_dataset: true,
    synthetic_dataset_percentage: percentage,
  });
const pageKey = () => getDatasetQueryKey(fixture.dataset, 0, [], [], "");
const detailUrl = () => endpoints.develop.getDatasetDetail(fixture.dataset);
const detailReads = () =>
  http.get.mock.calls.filter(([url]) => url === detailUrl());

let client;

function mount(ui, { search = "", seed } = {}) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  seed?.(client);
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <Routes>
        <Route path="/dashboard/develop/:dataset" element={ui} />
      </Routes>
    </QueryClientProvider>,
    { route: `/dashboard/develop/${fixture.dataset}${search}` },
  );
}

const firstAction = () =>
  screen.getByRole("button", { name: /new experiment|run experiment/i });
const reasonLine = () => screen.queryByTestId("first-experiment-reason");
const actionReady = () => waitFor(() => expect(firstAction()).toBeEnabled());

const realInitiateCreateMode =
  useRunExperimentStore.getState().initiateCreateMode;
let initiateCreateMode;

beforeEach(() => {
  Object.assign(fixture, {
    role: "Owner",
    canRun: true,
    runReason: null,
    classicShown: true,
    page: rowsPage(3),
    hold: false,
  });
  http.get.mockReset();
  http.get.mockImplementation((url) => {
    if (url !== detailUrl()) return Promise.resolve({ data: {} });
    if (fixture.hold) return new Promise(() => {});
    return Promise.resolve({ data: { result: fixture.page } });
  });
  initiateCreateMode = vi.fn(realInitiateCreateMode);
  useRunExperimentStore.setState({ initiateCreateMode });
  useChangeSetStore.setState({ sets: {} });
  useVersionDialogs.setState({ gate: null, save: null });
  useDatasetOriginStore.setState({ processingComplete: false });
});

afterEach(() => {
  cleanup();
  client?.clear();
  client = undefined;
  vi.useRealTimers();
  focusManager.setFocused(undefined);
  useRunExperimentStore.getState().reset();
  useRunExperimentStore.setState({
    initiateCreateMode: realInitiateCreateMode,
  });
  useChangeSetStore.setState({ sets: {} });
  useVersionDialogs.setState({ gate: null, save: null });
  useDatasetOriginStore.setState({ processingComplete: false });
});

describe("FR-EXPERIMENTS-03 classic Experiments tab first-run action", () => {
  it("shows the New experiment action under the first-run copy once this dataset's rows are read", async () => {
    mount(<ExperimentTabEmptyState />);
    expect(
      screen.getByText("Ready to run your first experiment"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Run your first experiment to test, compare, and improve prompt performance.",
      ),
    ).toBeInTheDocument();
    await actionReady();
    const button = firstAction();
    expect(button).toBeVisible();
    expect(button).toHaveTextContent("New experiment");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(reasonLine()).toBeNull();
  });

  it("opens the wizard once in create mode when there are no unsaved changes", async () => {
    mount(<ExperimentTabEmptyState />);
    await actionReady();
    fireEvent.click(firstAction());
    expect(initiateCreateMode).toHaveBeenCalledTimes(1);
    expect(initiateCreateMode).toHaveBeenCalledWith({
      prefill: undefined,
      firstRunType: undefined,
    });
    const store = useRunExperimentStore.getState();
    expect(store.openRunExperiment).toBe(true);
    expect(store.mode).toBe("create");
    expect(useVersionDialogs.getState().gate).toBe(null);
  });

  it("asks about unsaved dataset changes first, then opens the wizard once", async () => {
    useChangeSetStore.setState({
      sets: {
        [fixture.dataset]: {
          baseVersionId: null,
          ops: [{ id: "op_fr_1", op: "update_cell" }],
        },
      },
    });
    mount(<ExperimentTabEmptyState />);
    await actionReady();
    fireEvent.click(firstAction());
    const gate = useVersionDialogs.getState().gate;
    expect(gate).toMatchObject({
      datasetId: fixture.dataset,
      kind: "experiment",
    });
    expect(initiateCreateMode).not.toHaveBeenCalled();
    act(() => gate.proceed());
    expect(initiateCreateMode).toHaveBeenCalledTimes(1);
    expect(useRunExperimentStore.getState().openRunExperiment).toBe(true);
  });

  it("reads this dataset's own first page, not the first page of the dataset list", async () => {
    mount(<ExperimentTabEmptyState />);
    await actionReady();
    const reads = detailReads();
    expect(reads).toHaveLength(1);
    // The Data grid's page-0 read (same key and page size), which the
    // dataset header reads too.
    expect(reads[0][1].params).toMatchObject({
      current_page_index: 0,
      page_size: 30,
    });
    expect(client.getQueryData(pageKey())?.data?.result).toEqual(
      fixture.page,
    );
  });

  it("reads a cached empty first page again on mount and follows the saved rows", async () => {
    mount(<ExperimentTabEmptyState />, {
      // An earlier Data tab visit cached the page while the dataset was empty.
      seed: (c) =>
        c.setQueryData(pageKey(), { data: { result: rowsPage(0) } }),
    });
    // No old reason from the cached page before this mount's read answers.
    expect(firstAction()).toBeDisabled();
    expect(reasonLine()).toBeNull();
    await actionReady();
    expect(detailReads()).toHaveLength(1);
    expect(reasonLine()).toBeNull();
  });

  it("follows rows saved elsewhere when the window regains focus", async () => {
    fixture.page = rowsPage(0);
    mount(<ExperimentTabEmptyState />);
    expect(await screen.findByText("Add rows first")).toBeVisible();
    expect(firstAction()).toBeDisabled();
    // Rows saved through the SDK while this tab stays open.
    fixture.page = rowsPage(2);
    act(() => {
      focusManager.setFocused(false);
    });
    act(() => {
      focusManager.setFocused(true);
    });
    await actionReady();
    expect(reasonLine()).toBeNull();
    expect(detailReads()).toHaveLength(2);
  });

  it("follows rows saved elsewhere when the dataset read is invalidated", async () => {
    fixture.page = rowsPage(0);
    mount(<ExperimentTabEmptyState />);
    expect(await screen.findByText("Add rows first")).toBeVisible();
    fixture.page = rowsPage(1);
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ["dataset-detail", fixture.dataset],
      });
    });
    await actionReady();
    expect(reasonLine()).toBeNull();
  });

  it("holds the action on a cold link while synthetic rows are generated", async () => {
    // A flag left by another dataset says "complete"; this dataset's own
    // read decides.
    useDatasetOriginStore.setState({ processingComplete: true });
    fixture.page = generatingPage(40);
    mount(<ExperimentTabEmptyState />);
    expect(await screen.findByText("Rows are still processing")).toBeVisible();
    expect(firstAction()).toBeDisabled();
    await waitFor(() =>
      expect(useDatasetOriginStore.getState().processingComplete).toBe(false),
    );
  });

  it("polls while synthetic rows are generated and turns the action on when they are ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fixture.page = generatingPage(40);
    mount(<ExperimentTabEmptyState />);
    expect(await screen.findByText("Rows are still processing")).toBeVisible();
    expect(firstAction()).toBeDisabled();
    const before = detailReads().length;
    // Generation finishes on the server while the Experiments tab stays open.
    fixture.page = generatingPage(100);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await actionReady();
    expect(detailReads().length).toBeGreaterThan(before);
    expect(reasonLine()).toBeNull();
    // The dataset header reads the same flag.
    await waitFor(() =>
      expect(useDatasetOriginStore.getState().processingComplete).toBe(true),
    );
    // Polling stops once the rows are ready.
    const after = detailReads().length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(detailReads()).toHaveLength(after);
  });

  it("says why the action is off for a dataset with no rows", async () => {
    fixture.page = rowsPage(0);
    mount(<ExperimentTabEmptyState />);
    expect(await screen.findByText("Add rows first")).toBeVisible();
    expect(reasonLine()).toHaveTextContent("Add rows first");
    expect(firstAction()).toBeDisabled();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("shows no reason while this dataset's read has not answered", () => {
    fixture.hold = true;
    mount(<ExperimentTabEmptyState />);
    expect(firstAction()).toBeDisabled();
    expect(reasonLine()).toBeNull();
    expect(screen.queryByText("Add rows first")).toBeNull();
  });

  it("keeps the action on screen but disabled for a role without dataset update, and says why", async () => {
    fixture.role = "Viewer";
    mount(<ExperimentTabEmptyState />);
    const button = firstAction();
    expect(button).toBeVisible();
    expect(button).toBeDisabled();
    expect(reasonLine()).toHaveTextContent("Viewers can't run experiments");
    fireEvent.click(button);
    expect(initiateCreateMode).not.toHaveBeenCalled();
    await waitFor(() => expect(detailReads()).toHaveLength(1));
    expect(reasonLine()).toHaveTextContent("Viewers can't run experiments");
  });

  it("shows the run-access reason on the disabled action, on hover and on screen", () => {
    fixture.canRun = false;
    fixture.runReason = "You have view access to this dataset.";
    mount(<ExperimentTabEmptyState />);
    expect(firstAction()).toBeDisabled();
    expect(screen.getByTestId("tooltip-copy")).toHaveTextContent(
      "You have view access to this dataset.",
    );
    expect(reasonLine()).toHaveTextContent(
      "You have view access to this dataset.",
    );
  });

  it("offers no action in scenario mode, where the page mounts no wizard or pending-changes dialog", () => {
    mount(<ExperimentTabEmptyState />, { search: "?fromScenario=true" });
    expect(
      screen.getByText("Ready to run your first experiment"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(reasonLine()).toBeNull();
    expect(detailReads()).toHaveLength(0);
  });

  it("offers no action when the classic New experiment wizard is not shipped", () => {
    fixture.classicShown = false;
    mount(<ExperimentTabEmptyState />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(reasonLine()).toBeNull();
    expect(detailReads()).toHaveLength(0);
  });

  it("keeps the SDK-run line with no action under an SDK run's panel", () => {
    mount(<ExperimentTabEmptyState sdkRunShown />);
    expect(
      screen.getByTestId("experiment-tab-empty-with-sdk-run"),
    ).toHaveTextContent(SDK_COPY);
    expect(
      screen.queryByText("Ready to run your first experiment"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("keeps a caller's Run Experiment button when onRunExperiment is passed", () => {
    const onRunExperiment = vi.fn();
    mount(<ExperimentTabEmptyState onRunExperiment={onRunExperiment} />);
    fireEvent.click(screen.getByRole("button", { name: "Run Experiment" }));
    expect(onRunExperiment).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: "New experiment" }),
    ).not.toBeInTheDocument();
    expect(initiateCreateMode).not.toHaveBeenCalled();
    expect(detailReads()).toHaveLength(0);
  });
});
