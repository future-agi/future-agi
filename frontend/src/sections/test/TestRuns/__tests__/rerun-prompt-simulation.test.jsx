/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import TestRunsSelection from "../TestRunsSelection";

// Re-running a prompt simulation starts no calls in this release. This page
// also has Run New Simulation, whose first run does start calls, so the line
// points there.
const PROMPT_RERUN_LINE =
  "Re-run does not start calls for prompt simulations in this release. " +
  "Run the simulation again to get new calls.";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  snackbar: vi.fn(),
  refresh: vi.fn(),
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
      setToggledNodes: vi.fn(),
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
  const client = new QueryClient({
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
  // Every run on this page belongs to one run test, and a run test is either
  // prompt-based or not, so a selection is never mixed: the whole bulk
  // control is turned off, with the reason, rather than leaving runs out.
  it.each([
    ["source_type", { source_type: "prompt", prompt_template: "prompt-1" }],
    ["sourceType", { sourceType: "prompt", promptTemplate: "prompt-1" }],
  ])(
    "is turned off with the reason for several runs of a prompt simulation (%s)",
    (_field, testData) => {
      mocks.testData = testData;
      mocks.toggledNodes = ["run-1", "run-2", "run-3"];
      renderSelection();

      const rerun = screen.getByRole("button", {
        name: "Re-Run Simulation (3)",
      });
      expect(rerun).toBeDisabled();
      expect(rerun).toHaveAccessibleDescription(PROMPT_RERUN_LINE);

      fireEvent.click(rerun);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(mocks.post).not.toHaveBeenCalled();
    },
  );

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
