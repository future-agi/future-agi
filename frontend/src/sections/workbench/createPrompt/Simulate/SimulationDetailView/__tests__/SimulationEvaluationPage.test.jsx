import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import { SimulationDetailContext } from "../context/SimulationDetailContext";
import SimulationEvaluationPage from "../SimulationEvaluationPage";

const mocks = vi.hoisted(() => ({ post: vi.fn(), enqueueSnackbar: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post, patch: vi.fn(), delete: vi.fn() },
  endpoints: {
    runTests: {
      runEvals: (testId) => `/simulate/run-tests/${testId}/run-new-evals/`,
      deleteEvals: (testId, evalId) =>
        `/simulate/run-tests/${testId}/eval-configs/${evalId}/`,
    },
    promptSimulation: {
      detail: (templateId, simId) => `/prompts/${templateId}/sims/${simId}/`,
    },
  },
}));
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mocks.enqueueSnackbar,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// Every test run is selected in the grid, so "Run Evaluation" is enabled.
vi.mock("../states", () => ({
  useSimulationExecutionsGridStoreShallow: (selector) =>
    selector({ toggledNodes: [], selectAll: true }),
}));

const evalRow = (id, name) => ({
  id,
  name,
  eval_type: "llm",
  template_name: name,
  mapping: { output: "model_output" },
  eval_required_keys: ["output"],
});
const A = evalRow("e1", "toxicity");
const B = evalRow("e2", "groundedness");
const RUN_URL = "/simulate/run-tests/sim-1/run-new-evals/";

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SimulationDetailContext.Provider
        value={{
          simulationId: "sim-1",
          simulation: { id: "sim-1", simulate_eval_configs_detail: [A, B] },
          isLoading: false,
          refetchSimulation: vi.fn(),
        }}
      >
        <SimulationEvaluationPage onClose={vi.fn()} />
      </SimulationDetailContext.Provider>
    </QueryClientProvider>,
  );
};

const checkboxFor = (evalItem) =>
  screen.getByRole("checkbox", { name: `Select ${evalItem.name}` });

const openConfirm = async (user) => {
  await user.click(screen.getByRole("button", { name: "Run Evaluation" }));
  return screen.findByRole("dialog");
};

describe("SimulationEvaluationPage — choosing which evals to run", () => {
  beforeEach(() => {
    mocks.post.mockReset();
    mocks.post.mockResolvedValue({ data: {} });
  });

  it("runs only the checked evals, confirmed in the dialog first", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(checkboxFor(A)).not.toBeChecked();
    await user.click(checkboxFor(B));
    expect(screen.getByText("1 of 2 selected")).toBeInTheDocument();

    const dialog = await openConfirm(user);
    expect(await within(dialog).findByText(B.name)).toBeInTheDocument();
    expect(within(dialog).queryByText(A.name)).toBeNull();

    await user.click(
      within(dialog).getByRole("button", { name: "Run Evaluations" }),
    );
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        RUN_URL,
        expect.objectContaining({ eval_config_ids: [B.id] }),
      ),
    );
  });

  it("runs every eval when none is checked", async () => {
    const user = userEvent.setup();
    renderPage();

    const dialog = await openConfirm(user);
    expect(await within(dialog).findByText(A.name)).toBeInTheDocument();
    expect(within(dialog).getByText(B.name)).toBeInTheDocument();

    await user.click(
      within(dialog).getByRole("button", { name: "Run Evaluations" }),
    );
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        RUN_URL,
        expect.objectContaining({ eval_config_ids: [A.id, B.id] }),
      ),
    );
  });
});
