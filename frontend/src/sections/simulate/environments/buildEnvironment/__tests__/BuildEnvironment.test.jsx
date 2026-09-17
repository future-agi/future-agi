import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  waitFor,
  act,
} from "@testing-library/react";
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

// The single real POST the preflight makes; listHarnessJobs is imported at
// module scope by environments.js (useBuildEnvironment / useRunSimulation).
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  getHarnessJob: vi.fn(),
}));

// A controllable useBuildProgress: record the args, return whatever the current
// test set. The heavy timer-driven internals are T8/T16's concern.
const buildProgressCalls = vi.fn();
let progressReturn;
vi.mock("src/api/simulate-environments/buildProgress", () => ({
  useBuildProgress: (args) => {
    buildProgressCalls(args);
    return progressReturn;
  },
}));

// The hero animation runs an 850ms interval; echo its label so the pending
// assertion is crisp and no timer noise leaks into the test.
vi.mock("../building/DerivingAnimation", () => ({
  default: ({ label }) => <div data-testid="deriving">{label}</div>,
}));

const { preflightHarnessJob } = await import("src/api/harness/harness");
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
const { READ_AUDIT_COPY } = await import("../readAudit.constants");
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
const uploadDraft = { kind: "upload", entry: "src/agent.py", files: [] };

// A plain happy preflight — the mock overlay fills reading / questions / gaps.
const HAPPY = {
  ready_to_submit: true,
  credentials: {
    scanned_files: 42,
    detected_connectors: ["vapi"],
    requirements: [],
    credential_choices: [],
    probe: [{ provider: "vapi", ok: true }],
  },
  packaging: { notes: [], candidates: [], selected_path: "src/agent.py" },
};

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

// The full happy path up to the building stage: preflight → answer both
// questions → Build the environment.
async function drivePreflightToBuild(user) {
  useEnvironmentsStore.setState({ draft: repoDraft });
  preflightHarnessJob.mockResolvedValue(HAPPY);
  const utils = renderPage();

  await screen.findByText(READ_AUDIT_COPY.title);
  await user.click(screen.getByRole("button", { name: /Read-only/ }));
  await user.click(screen.getByRole("button", { name: /Yes/ }));
  await user.click(screen.getByRole("button", { name: /Build the environment/ }));

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
async function driveToWorkspace(user) {
  progressReturn = {
    ...emptyProgress(),
    done: [...ALL_DONE],
  };
  const utils = await drivePreflightToBuild(user);
  act(() =>
    useEnvironmentsStore.getState().setBuildProgress({
      done: [...ALL_DONE],
      running: false,
      failure: null,
    }),
  );
  // The hero is gone the moment the workspace panels take over the body.
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
  progressReturn = emptyProgress();
});

describe("BuildEnvironment", () => {
  it("redirects to the Build tab (replace) when there is no draft", async () => {
    renderPage();
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(buildTab, { replace: true }),
    );
  });

  it("preflights a repo draft: pending hero → read-audit, header pill, no Run", async () => {
    let resolvePreflight;
    preflightHarnessJob.mockReturnValue(
      new Promise((res) => {
        resolvePreflight = res;
      }),
    );
    useEnvironmentsStore.setState({ draft: repoDraft });
    renderPage();

    // Fires the one POST with the T5 payload.
    await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalledTimes(1));
    expect(preflightHarnessJob).toHaveBeenCalledWith(
      draftToPreflightPayload(repoDraft).payload,
    );

    // While pending: the "Reading your agent…" hero.
    expect(screen.getByTestId("deriving")).toHaveTextContent(DERIVING_LABEL.idle);

    await act(async () => {
      resolvePreflight(HAPPY);
    });

    // Read-audit takes over, header pill reads "Setup being built", no Run yet.
    expect(await screen.findByText(READ_AUDIT_COPY.title)).toBeInTheDocument();
    expect(screen.getByText(/Reader completed with 2 gaps/)).toBeInTheDocument();
    expect(screen.getByText(BUILD_HEADER_COPY.setupBuilding)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: BUILD_HEADER_COPY.run }),
    ).not.toBeInTheDocument();
  });

  it("accepts the audit → building state, minted envId, both answers, wired progress", async () => {
    const user = userEvent.setup();
    await drivePreflightToBuild(user);

    const state = useEnvironmentsStore.getState();
    expect(state.envId).toMatch(/^env-/);
    expect(Object.keys(state.readerAnswers)).toEqual(
      expect.arrayContaining(["tool-side-effects", "policy-enforcement"]),
    );

    // The building stage renders the console and the muted tab rail.
    expect(screen.getByPlaceholderText("Reply to the builder…")).toBeInTheDocument();
    BUILDING_TABS.forEach((t) =>
      expect(screen.getByText(t.label)).toBeInTheDocument(),
    );

    // useBuildProgress is now enabled and carries the agent ref.
    expect(buildProgressCalls).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: true, agentRef: "acme/support-bot@main" }),
    );
  });

  it("swaps the body in place at 7/7: interactive rail, Summary renders the env", async () => {
    const user = userEvent.setup();
    await driveToWorkspace(user);

    // The muted, pointer-dead loading rail is gone; the live workspace rail and
    // the Summary body are in its place, rendering the environment's world.
    expect(await screen.findByRole("tab", { name: /^Summary/ })).toBeInTheDocument();
    expect(
      screen.getByText(/returns-and-orders phone line/i),
    ).toBeInTheDocument();
    // The env name still lives in the build header (also echoed in the console).
    expect(screen.getAllByText("support-bot").length).toBeGreaterThan(0);
  });

  it("adopts the environment into the store once at 7/7", async () => {
    const user = userEvent.setup();
    await driveToWorkspace(user);

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
    await driveToWorkspace(user);

    await user.click(screen.getByRole("tab", { name: /^Scenarios/ }));

    expect(screen.getByText(`(${POOL_SIZE})`)).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("Run simulation navigates to the product run-tests entry", async () => {
    const user = userEvent.setup();
    await driveToWorkspace(user);

    expect(await screen.findByText(BUILD_HEADER_COPY.ready)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: BUILD_HEADER_COPY.run }));

    expect(navigate).toHaveBeenCalledWith(paths.dashboard.simulate.test);
    expect(navigate).not.toHaveBeenCalledWith(
      expect.stringContaining("simulate/environments"),
    );
  });

  it("does not re-seed the env state on a stale remount", async () => {
    const user = userEvent.setup();
    const { unmount } = await driveToWorkspace(user);
    const envId = useEnvironmentsStore.getState().envId;

    // The reader edits the seeded scenarios down to nothing.
    act(() =>
      useEnvironmentsStore.getState().patchEnvState(envId, { scenarios: [] }),
    );
    expect(useEnvironmentsStore.getState().byEnv[envId].scenarios).toHaveLength(0);

    // A remount reads the stale building slice (envId + all-done progress) on
    // its first render; startPreflight resets the build slice, but the adoption
    // effect closure still points at envId. The already-adopted guard must stop
    // it from re-seeding over the reader's edit.
    unmount();
    renderPage();
    await waitFor(() =>
      expect(useEnvironmentsStore.getState().buildStage).toBe("preflight"),
    );

    expect(useEnvironmentsStore.getState().byEnv[envId].scenarios).toHaveLength(0);
    expect(Object.keys(useEnvironmentsStore.getState().workspaceEnvs)).toEqual([
      envId,
    ]);
  });

  it("back navigates to the Build tab and never resets the draft on unmount", async () => {
    const user = userEvent.setup();
    preflightHarnessJob.mockResolvedValue(HAPPY);
    useEnvironmentsStore.setState({ draft: repoDraft });
    const { unmount } = renderPage();

    await user.click(screen.getByRole("button", { name: BUILD_HEADER_COPY.back }));
    expect(navigate).toHaveBeenCalledWith(buildTab);

    unmount();
    expect(useEnvironmentsStore.getState().draft).toEqual(repoDraft);
  });

  it("resets a stale building slice to preflight on mount", async () => {
    preflightHarnessJob.mockResolvedValue(HAPPY);
    useEnvironmentsStore.setState({
      draft: repoDraft,
      buildStage: "building",
      envId: "env-stale",
    });
    renderPage();

    await waitFor(() =>
      expect(useEnvironmentsStore.getState().buildStage).toBe("preflight"),
    );
    expect(useEnvironmentsStore.getState().envId).toBeNull();
  });

  it("skips preflight for an upload draft and shows the skipped issue", async () => {
    useEnvironmentsStore.setState({ draft: uploadDraft });
    renderPage();

    expect(await screen.findByText("Preflight skipped")).toBeInTheDocument();
    expect(preflightHarnessJob).not.toHaveBeenCalled();
  });
});
