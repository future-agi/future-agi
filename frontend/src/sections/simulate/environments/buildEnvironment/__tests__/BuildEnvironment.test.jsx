import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { palette } from "src/theme/palette";
import { paths } from "src/routes/paths";

// The section's convention: mock useNavigate as a spy, keep the rest of the
// router real so MemoryRouter / useSearchParams still work.
const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// notistack — keep the real module (the workspace tree pulls the snackbar
// provider, which needs MaterialDesignContent) and only spy enqueueSnackbar.
const enqueueSnackbar = vi.fn();
vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, enqueueSnackbar: (...a) => enqueueSnackbar(...a) };
});

// The build page no longer runs preflight — that happens inline below the source
// form. The only POST it owns is the create (createHarnessJob, via
// useBuildEnvironment). listHarnessJobs is imported at module scope by
// environments.js; preflightHarnessJob is kept so we can assert it is NEVER hit.
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  getHarnessJob: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
}));

// A controllable useBuildProgress: record the args, return whatever the current
// test set. The heavy timer-driven internals are the progress hook's concern.
const buildProgressCalls = vi.fn();
let progressReturn;
vi.mock("src/api/simulate-environments/buildProgress", () => ({
  useBuildProgress: (args) => {
    buildProgressCalls(args);
    return progressReturn;
  },
}));

// The hero animation runs an interval; echo its label so the pending assertion
// is crisp and no timer noise leaks into the test.
vi.mock("../building/DerivingAnimation", () => ({
  default: ({ label }) => <div data-testid="deriving">{label}</div>,
}));

const { preflightHarnessJob, createHarnessJob } = await import(
  "src/api/harness/harness"
);
const { draftToPreflightPayload } = await import(
  "src/api/simulate-environments/preflightPayload"
);
const { default: BuildEnvironment } = await import("../BuildEnvironment");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);
const { BUILD_HEADER_COPY, DERIVING_LABEL, BUILDING_TABS } = await import(
  "../build.constants"
);
const { generatedPool } = await import(
  "src/api/simulate-environments/_fixtures/scenarioPool"
);
const { MOCK_WORLD } = await import("src/api/simulate-environments/_fixtures/world");

// The full scenario pool the build seeds from MOCK_WORLD — the count the
// Scenarios tab renders once the workspace swaps in at 7/7.
const POOL_SIZE = generatedPool(MOCK_WORLD).length;

const theme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

const repoDraft = { kind: "repo", value: "acme/support-bot", ref: "main" };

// The passing preflight response the panel would have handed off. The build page
// never re-reads it (create is built from the draft) — it only proves a real
// handoff happened, so its exact shape is immaterial here.
const HAPPY = { ready_to_submit: true, state: "connected", checks: [] };

const emptyProgress = () => ({
  done: [],
  running: false,
  failure: null,
  turns: [],
  chips: [],
  send: vi.fn(),
  onChip: vi.fn(),
});

const buildTab = `${paths.dashboard.simulate.environments.root}?tab=build`;

function renderPage(route = "/dashboard/simulate/environments/build") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <ThemeProvider theme={theme}>
          <CssBaseline />
          <BuildEnvironment />
        </ThemeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Stage the one-shot build ticket the panel produces after inline preflight
// passes, exactly as beginBuild does.
function stageTicket(draft = repoDraft, preflight = HAPPY) {
  useEnvironmentsStore.getState().beginBuild({ draft, preflight });
}

// Stage the ticket, render, and wait for the create to land the building stage.
async function driveToBuilding() {
  stageTicket();
  const utils = renderPage();
  await waitFor(() =>
    expect(useEnvironmentsStore.getState().buildStage).toBe("building"),
  );
  return utils;
}

const ALL_DONE = ["understand", "build", "scenarios"];

// Drive the build to 7/7 with both progress sources set: the store's
// buildProgress (which BuildEnvironment reads for the header + adoption) and the
// mocked useBuildProgress return (which BuildingPane reads for its swap). In
// production useBuildProgress writes the store, so the two are always in step.
async function driveToWorkspace() {
  progressReturn = { ...emptyProgress(), done: [...ALL_DONE] };
  const utils = await driveToBuilding();
  act(() =>
    useEnvironmentsStore.getState().setBuildProgress({
      done: [...ALL_DONE],
      running: false,
      failure: null,
    }),
  );
  await waitFor(() =>
    expect(screen.queryByTestId("deriving")).not.toBeInTheDocument(),
  );
  return utils;
}

beforeAll(() => {
  // jsdom has no layout; the console auto-scrolls to its latest turn.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  resetEnvironmentsStore();
  navigate.mockReset();
  enqueueSnackbar.mockReset();
  buildProgressCalls.mockReset();
  preflightHarnessJob.mockReset();
  createHarnessJob.mockReset();
  // The create mints the real job; the minted env id is the server's job id.
  createHarnessJob.mockResolvedValue({ job: { job_id: "job-real" } });
  progressReturn = emptyProgress();
});

describe("BuildEnvironment", () => {
  it("bounces to the Build tab (replace) when there is no pending ticket", async () => {
    renderPage();
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(buildTab, { replace: true }),
    );
    expect(createHarnessJob).not.toHaveBeenCalled();
  });

  it("bounces even when a stale persisted draft is present but no ticket", async () => {
    // A refresh rehydrates `draft` from sessionStorage but not `pendingBuild`.
    useEnvironmentsStore.setState({ draft: repoDraft, pendingBuild: null });
    renderPage();
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(buildTab, { replace: true }),
    );
    expect(createHarnessJob).not.toHaveBeenCalled();
  });

  it("never runs preflight on the build page", async () => {
    await driveToBuilding();
    expect(preflightHarnessJob).not.toHaveBeenCalled();
  });

  it("shows the creating hero while the create is in flight", async () => {
    let resolveCreate;
    createHarnessJob.mockReturnValue(
      new Promise((res) => {
        resolveCreate = res;
      }),
    );
    stageTicket();
    renderPage();

    expect(screen.getByTestId("deriving")).toHaveTextContent(
      DERIVING_LABEL.creating,
    );
    await act(async () => {
      resolveCreate({ job: { job_id: "job-real" } });
    });
    await waitFor(() =>
      expect(useEnvironmentsStore.getState().buildStage).toBe("building"),
    );
  });

  it("creates the job once from the ticket draft, mints the env id, wires progress", async () => {
    await driveToBuilding();

    const state = useEnvironmentsStore.getState();
    expect(state.envId).toBe("job-real");
    expect(state.buildStage).toBe("building");
    // No reader questions anymore — the accepted answers are empty.
    expect(state.readerAnswers).toEqual({});
    expect(createHarnessJob).toHaveBeenCalledTimes(1);
    expect(createHarnessJob).toHaveBeenCalledWith(
      draftToPreflightPayload(repoDraft).payload,
      "idem-test",
    );
    // The ticket is consumed — a refresh can't re-fire create.
    expect(state.pendingBuild).toBeNull();

    // The building stage renders the console and the muted tab rail.
    expect(screen.getByPlaceholderText("Reply to the builder…")).toBeInTheDocument();
    BUILDING_TABS.forEach((t) =>
      expect(screen.getByText(t.label)).toBeInTheDocument(),
    );
    expect(buildProgressCalls).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: true, agentRef: "acme/support-bot@main" }),
    );
  });

  it("surfaces a create failure and bounces back to the source form", async () => {
    createHarnessJob.mockRejectedValue(new Error("ALK sandbox is unavailable"));
    stageTicket();
    renderPage();

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("ALK sandbox is unavailable", {
        variant: "error",
      }),
    );
    expect(navigate).toHaveBeenCalledWith(buildTab, { replace: true });
    // Nothing was accepted or minted.
    const state = useEnvironmentsStore.getState();
    expect(state.buildStage).toBe("preflight");
    expect(state.envId).toBeNull();
  });

  it("does not mint a second job on a remount (one-shot ticket)", async () => {
    const { unmount } = await driveToBuilding();
    expect(createHarnessJob).toHaveBeenCalledTimes(1);

    // A remount (refresh) finds no ticket and bounces — no second create.
    unmount();
    renderPage();
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(buildTab, { replace: true }),
    );
    expect(createHarnessJob).toHaveBeenCalledTimes(1);
  });

  it("swaps the body in place at 7/7: interactive rail, Summary renders the env", async () => {
    await driveToWorkspace();

    expect(await screen.findByRole("tab", { name: /^Summary/ })).toBeInTheDocument();
    expect(screen.getByText(/returns-and-orders phone line/i)).toBeInTheDocument();
    expect(screen.getAllByText("support-bot").length).toBeGreaterThan(0);
  });

  it("adopts the environment into the store once at 7/7", async () => {
    await driveToWorkspace();

    const state = useEnvironmentsStore.getState();
    const envId = state.envId;
    expect(state.workspaceEnvs[envId]).toMatchObject({
      id: envId,
      buildStatus: "ready",
      name: "support-bot",
    });
    expect(state.byEnv[envId].agentVersions[0].label).toBe("v1");
    expect(state.byEnv[envId].scenarios).toHaveLength(POOL_SIZE);
  });

  it("opens the Scenarios tab from the live rail and lists the seeded pool", async () => {
    const user = userEvent.setup();
    await driveToWorkspace();

    await user.click(screen.getByRole("tab", { name: /^Scenarios/ }));
    expect(screen.getByText(`(${POOL_SIZE})`)).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("Run simulation navigates to the product run-tests entry", async () => {
    const user = userEvent.setup();
    await driveToWorkspace();

    expect(await screen.findByText(BUILD_HEADER_COPY.ready)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: BUILD_HEADER_COPY.run }));

    expect(navigate).toHaveBeenCalledWith(paths.dashboard.simulate.test);
    expect(navigate).not.toHaveBeenCalledWith(
      expect.stringContaining("simulate/environments"),
    );
  });

  it("does not re-seed the env state on a stale remount", async () => {
    const { unmount } = await driveToWorkspace();
    const envId = useEnvironmentsStore.getState().envId;

    // Edit the seeded scenarios down to nothing.
    act(() =>
      useEnvironmentsStore.getState().patchEnvState(envId, { scenarios: [] }),
    );
    expect(useEnvironmentsStore.getState().byEnv[envId].scenarios).toHaveLength(0);

    // A remount with no ticket bounces immediately; the building slice + all-done
    // progress still sit in the store, so the adoption effect closure re-runs on
    // the fresh mount — the already-adopted (`env` present) guard must stop it
    // from re-seeding over the edit.
    unmount();
    renderPage();
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(buildTab, { replace: true }),
    );
    expect(useEnvironmentsStore.getState().byEnv[envId].scenarios).toHaveLength(0);
    expect(Object.keys(useEnvironmentsStore.getState().workspaceEnvs)).toEqual([
      envId,
    ]);
  });

  it("back navigates to the Build tab and never resets the draft on unmount", async () => {
    const user = userEvent.setup();
    const { unmount } = await driveToBuilding();

    await user.click(screen.getByRole("button", { name: BUILD_HEADER_COPY.back }));
    expect(navigate).toHaveBeenCalledWith(buildTab);

    unmount();
    expect(useEnvironmentsStore.getState().draft).toEqual(repoDraft);
  });
});
