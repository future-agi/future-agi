/* eslint-disable react/prop-types */
import React from "react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import TestRunsSelection from "../TestRunsSelection";

// What the server answers (400) when a prompt simulation is re-run while it
// has no prompt version selected.
const NO_VERSION_REFUSAL =
  "Select a prompt version, then re-run this simulation.";

// That refusal as the app's request client hands it to the caller.
const noVersionRefusal = () => ({
  status: false,
  type: "validation_error",
  code: "invalid",
  detail: NO_VERSION_REFUSAL,
  message: NO_VERSION_REFUSAL,
  error: NO_VERSION_REFUSAL,
  result: NO_VERSION_REFUSAL,
  statusCode: 400,
});

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  snackbar: vi.fn(),
  refresh: vi.fn(),
  setToggledNodes: vi.fn(),
  setOpenTestEvaluation: vi.fn(),
  testData: null,
  toggledNodes: [],
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    runTests: {
      rerunSimulation: (id) =>
        `/simulate/run-tests/${id}/rerun-test-executions/`,
      deleteSimulation: (id) =>
        `/simulate/run-tests/${id}/delete-test-executions/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ testId: "test-1" }),
}));
vi.mock("../../context/TestDetailContext", () => ({
  useTestDetailContext: () => ({
    testData: mocks.testData,
    refreshTestRunGrid: mocks.refresh,
  }),
}));
vi.mock("../../states", () => ({
  useTestRunsGridStoreShallow: (selector) =>
    selector({
      toggledNodes: mocks.toggledNodes,
      setToggledNodes: mocks.setToggledNodes,
      setSelectAll: vi.fn(),
    }),
  useTestEvaluationStoreShallow: (selector) =>
    selector({ setOpenTestEvaluation: mocks.setOpenTestEvaluation }),
}));
vi.mock("../../common", () => ({
  useTestRunsSelectedCount: () => mocks.toggledNodes.length,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: ({ open, title, subTitle, onSubmit, actionBtnTitle }) =>
    open ? (
      <div role="dialog" aria-label={title}>
        <p>{subTitle}</p>
        <button type="button" onClick={onSubmit}>
          {actionBtnTitle}
        </button>
      </div>
    ) : null,
}));

const clients = [];
const renderSelection = () => {
  // The app's client: a failed request is reported by the app's error toast.
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <TestRunsSelection />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.testData = null;
  mocks.toggledNodes = [];
  mocks.post.mockResolvedValue({ data: {} });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("Re-Run Simulation on the selected runs of a run test", () => {
  // The server makes a prompt simulation's calls again on a re-run, so the
  // selected runs re-run exactly as an agent simulation's do.
  it.each([
    ["source_type", { source_type: "prompt", prompt_template: "prompt-1" }],
    ["sourceType", { sourceType: "prompt", promptTemplate: "prompt-1" }],
  ])(
    "re-runs the calls and evals of the selected runs of a prompt simulation (%s)",
    async (_field, testData) => {
      mocks.testData = testData;
      mocks.toggledNodes = ["run-1", "run-2", "run-3"];
      renderSelection();

      const rerun = screen.getByRole("button", {
        name: "Re-Run Simulation (3)",
      });
      expect(rerun).not.toBeDisabled();
      expect(rerun).not.toHaveAttribute("aria-describedby");

      fireEvent.click(rerun);
      const dialog = screen.getByRole("dialog", {
        name: "Re-Run Simulations",
      });
      fireEvent.click(within(dialog).getByRole("button", { name: "Re-Run" }));

      await waitFor(() =>
        expect(mocks.post).toHaveBeenCalledWith(
          "/simulate/run-tests/test-1/rerun-test-executions/",
          {
            test_execution_ids: ["run-1", "run-2", "run-3"],
            select_all: false,
            rerun_type: "call_and_eval",
          },
        ),
      );
      await waitFor(() =>
        expect(mocks.snackbar).toHaveBeenCalledWith(
          "Simulation re-run initiated successfully",
          { variant: "success" },
        ),
      );
      expect(mocks.setToggledNodes).toHaveBeenCalledWith([]);
      expect(mocks.refresh).toHaveBeenCalled();
    },
  );

  it("shows the server's sentence when a prompt simulation has no prompt version selected", async () => {
    mocks.testData = { source_type: "prompt", prompt_template: "prompt-1" };
    mocks.toggledNodes = ["run-1", "run-2"];
    mocks.post.mockRejectedValueOnce(noVersionRefusal());
    renderSelection();

    fireEvent.click(
      screen.getByRole("button", { name: "Re-Run Simulation (2)" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Re-Run Simulations" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Re-Run" }));

    await waitFor(() =>
      expect(mocks.snackbar).toHaveBeenCalledWith(NO_VERSION_REFUSAL, {
        variant: "error",
      }),
    );
    expect(mocks.snackbar).toHaveBeenCalledTimes(1);
    expect(mocks.snackbar).not.toHaveBeenCalledWith(
      "Something went wrong",
      expect.anything(),
    );
    // The selection and the open confirm stay, so the runs can be re-run
    // once a version is selected.
    expect(mocks.setToggledNodes).not.toHaveBeenCalled();
    expect(mocks.refresh).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "Re-Run Simulations" }),
    ).toBeInTheDocument();
  });

  it("keeps Run Evals and Delete for the selected runs of a prompt simulation", () => {
    mocks.testData = { source_type: "prompt", prompt_template: "prompt-1" };
    mocks.toggledNodes = ["run-1", "run-2"];
    renderSelection();

    expect(
      screen.getByRole("button", { name: "Run Evals (2)" }),
    ).not.toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Delete (2)" }),
    ).not.toBeDisabled();
  });

  it.each([
    [
      "an agent simulation",
      { source_type: "agent_definition", agent_definition: "agent-1" },
    ],
    ["a run test whose details have not loaded", null],
  ])(
    "re-runs the selected runs of %s exactly as before",
    async (_label, testData) => {
      mocks.testData = testData;
      mocks.toggledNodes = ["run-1", "run-2"];
      renderSelection();

      const rerun = screen.getByRole("button", {
        name: "Re-Run Simulation (2)",
      });
      expect(rerun).not.toBeDisabled();
      expect(rerun).not.toHaveAttribute("aria-describedby");

      fireEvent.click(rerun);
      const dialog = screen.getByRole("dialog", {
        name: "Re-Run Simulations",
      });
      fireEvent.click(within(dialog).getByRole("button", { name: "Re-Run" }));

      await waitFor(() =>
        expect(mocks.post).toHaveBeenCalledWith(
          "/simulate/run-tests/test-1/rerun-test-executions/",
          {
            test_execution_ids: ["run-1", "run-2"],
            select_all: false,
            rerun_type: "call_and_eval",
          },
        ),
      );
    },
  );
});
