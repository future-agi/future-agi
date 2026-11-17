import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

// user-info carries the Simulate cutoff flag for the organization.
const authState = { user: {} };
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  harnessIdempotencyKey: vi.fn(() => "key-1"),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

// The panels stand in for every Run simulation / Start simulation button:
// each one starts the run through the workspace's onStartRun.
function PanelsStub({ onStartRun }) {
  return (
    <button type="button" onClick={() => onStartRun?.(["s1"], 1)}>
      start simulation
    </button>
  );
}
PanelsStub.propTypes = { onStartRun: PropTypes.func };
vi.mock("../WorkspacePanels", () => ({ default: PanelsStub }));

const { enqueueSnackbar } = await import("notistack");
const { default: axios } = await import("src/utils/axios");
const { default: EnvironmentWorkspace } = await import(
  "../EnvironmentWorkspace"
);
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);
const { emptyEnvState } = await import("../../store/envState");

// A client environment: not harness-sourced, so a run leaves the workspace.
const CLIENT_ENV = {
  id: "env-1",
  name: "Refund Copilot",
  surface: "voice",
  tagline: "Inbound phone support for an online storefront",
  tools: [{ name: "issue_refund", desc: "Issues a refund." }],
  rules: ["Never refund twice"],
  seed: { tables: [{ name: "orders", rows: 500 }] },
  evalPreset: ["task_success", "tone"],
};
// The same environment once it carries the run it was built with.
const CLIENT_ENV_WITH_RUN = {
  ...CLIENT_ENV,
  platform: { runTestId: "rt1", testExecutionId: "ex1" },
};

const LINE =
  "Simulations for this environment start in Run Simulation, which your organization doesn't have.";

function LocationProbe() {
  const { pathname } = useLocation();
  return <div data-testid="location">{pathname}</div>;
}

const appTheme = createTheme({ palette: palette("light") });

const renderWorkspace = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  return render(
    <ThemeProvider theme={appTheme}>
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={["/dashboard/simulate/environments/env-1"]}
        >
          <LocationProbe />
          <Routes>
            <Route
              path="/dashboard/simulate/environments/:envId"
              element={<EnvironmentWorkspace />}
            />
            <Route path="*" element={null} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
};

const seedClientEnv = (env) =>
  useEnvironmentsStore.setState({
    workspaceEnvs: { [env.id]: env },
    byEnv: {
      [env.id]: {
        ...emptyEnvState(),
        agent: { name: "Support agent" },
        scenarios: [{ id: "s1" }],
      },
    },
  });

const startSimulation = async () => {
  const user = userEvent.setup();
  renderWorkspace();
  await user.click(
    await screen.findByRole("button", { name: "start simulation" }),
  );
};

const location = () => screen.getByTestId("location");

describe("EnvironmentWorkspace run start outside the harness", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    resetEnvironmentsStore();
    authState.user = {};
    enqueueSnackbar.mockReset();
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data: { results: [], count: 0 } });
    axios.post.mockReset();
  });

  describe("for an organization that sees only Environments", () => {
    beforeEach(() => {
      authState.user = { restrict_simulate_to_environments: true };
    });

    it("opens the environment's own run page", async () => {
      seedClientEnv(CLIENT_ENV_WITH_RUN);
      await startSimulation();

      await waitFor(() =>
        expect(location()).toHaveTextContent(
          "/dashboard/simulate/environments/env-1/runs/rt1/ex1",
        ),
      );
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    });

    it("stays on the environment and says why when it has no run to open", async () => {
      seedClientEnv(CLIENT_ENV);
      await startSimulation();

      await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalled());
      expect(enqueueSnackbar).toHaveBeenCalledWith(LINE, { variant: "info" });
      expect(location()).toHaveTextContent(
        /^\/dashboard\/simulate\/environments\/env-1$/,
      );
    });
  });

  describe("for every other organization", () => {
    it("opens the run's Run Simulation page", async () => {
      seedClientEnv(CLIENT_ENV_WITH_RUN);
      await startSimulation();

      await waitFor(() =>
        expect(location()).toHaveTextContent(
          "/dashboard/simulate/test/rt1/ex1/call-details",
        ),
      );
    });

    it("opens Run Simulation when the environment has no run", async () => {
      seedClientEnv(CLIENT_ENV);
      await startSimulation();

      await waitFor(() =>
        expect(location()).toHaveTextContent(/^\/dashboard\/simulate\/test$/),
      );
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    });
  });
});
