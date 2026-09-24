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

// notistack — the Run CTA fires enqueueSnackbar on success.
const enqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({ enqueueSnackbar: (...a) => enqueueSnackbar(...a) }));

// The single real POST the preflight makes; listHarnessJobs is imported at
// module scope by environments.js (useBuildEnvironment / useRunSimulation).
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
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
const { RUN_SIMULATION_COPY } = await import("../../environmentOptions");

const theme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

const repoDraft = { kind: "repo", value: "acme/support-bot", ref: "main" };
const uploadDraft = { kind: "upload", entry: "src/agent.py", files: [] };

// A plain happy preflight: every check passed, nothing to flag.
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

// The full happy path up to the building stage: preflight → Build the
// environment. The real response carries no open questions, so the audit's own
// build CTA is the only step between the read-audit and the build.
async function drivePreflightToBuild(user) {
  useEnvironmentsStore.setState({ draft: repoDraft });
  preflightHarnessJob.mockResolvedValue(HAPPY);
  renderPage();

  await screen.findByText(READ_AUDIT_COPY.title);
  await user.click(screen.getByRole("button", { name: READ_AUDIT_COPY.build }));

  await waitFor(() =>
    expect(useEnvironmentsStore.getState().buildStage).toBe("building"),
  );
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
    // A clean preflight reports no gaps — nothing invented on the user's behalf.
    expect(screen.queryByText(/Reader completed with/)).not.toBeInTheDocument();
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
    expect(state.readerAnswers).toEqual({});

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

  it("drives the pipeline to all-done → Run simulation fires the snackbar", async () => {
    const user = userEvent.setup();
    await drivePreflightToBuild(user);

    act(() =>
      useEnvironmentsStore.getState().setBuildProgress({
        done: ["understand", "build", "scenarios"],
        running: false,
        failure: null,
      }),
    );

    expect(await screen.findByText(BUILD_HEADER_COPY.ready)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: BUILD_HEADER_COPY.run }));

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(RUN_SIMULATION_COPY, {
        variant: "info",
      }),
    );
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
