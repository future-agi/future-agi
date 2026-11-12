import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import ReadsOnSampleTable from "src/sections/develop-detail/Experiment/StepperComponentExperiment/ReadsOnSampleTable";
import { EVAL_MAPPING_PREVIEW } from "../useEvalMappingPreview";

const http = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: http }));
vi.mock("src/sections/develop-detail/Experiment/ExperimentFormContext", () => ({
  useExperimentForm: () => ({ columns: [] }),
}));

const evaluation = {
  templateId: "template-id",
  name: "Preview judge",
  config: {
    mapping: { question: "column-id", answer: "output" },
    version_pin: "v1",
  },
};
function Form({ pending = false, evals = [evaluation] }) {
  const { control } = useForm({
    defaultValues: { userEvalMetrics: evals, promptConfig: [] },
  });
  return (
    <ReadsOnSampleTable
      control={control}
      datasetId="dataset-id"
      sampleRow={{ row_id: "row-id", ...(pending ? { pending_no: 1 } : {}) }}
    />
  );
}
Form.propTypes = { pending: PropTypes.bool, evals: PropTypes.array };
const mount = (pending = false, evals = [evaluation]) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Form pending={pending} evals={evals} />
    </QueryClientProvider>,
  );
const shown = () =>
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: ["exp.new.savedInputReads"],
  });

beforeEach(() => {
  http.post.mockReset().mockResolvedValue({
    data: {
      result: {
        rows: [
          {
            row_id: "row-id",
            inputs_used: {
              question: {
                column: "question",
                value: 0,
                status: "ok",
                note: null,
              },
              answer: {
                column: "run output",
                value: null,
                status: "pending",
                note: "Available after the run",
              },
              expected: {
                column: null,
                value: null,
                status: "missing",
                note: "Not mapped",
              },
            },
          },
        ],
      },
    },
  });
});
afterEach(() => setLaunchOverrideForTest(null));

it("does not request or render an unclaimed preview", async () => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: [],
  });
  mount();
  await act(async () => {});
  expect(screen.queryByTestId("reads-on-sample")).toBeNull();
  expect(http.post).not.toHaveBeenCalled();
});

it("only calls the read-only API and renders values without invented skip rules", async () => {
  shown();
  mount();
  await waitFor(() => expect(screen.getByText("= 0")).toBeInTheDocument());
  expect(http.post).toHaveBeenCalledExactlyOnceWith(EVAL_MAPPING_PREVIEW, {
    dataset_id: "dataset-id",
    template_id: "template-id",
    row_ids: ["row-id"],
    config: evaluation.config,
  });
  expect(screen.getByText("answer ← run output")).toBeInTheDocument();
  expect(screen.getByText("· Available after the run")).toBeInTheDocument();
  expect(screen.getByText("· Not mapped")).toBeInTheDocument();
  expect(screen.queryByText(/skipped/i)).toBeNull();
});

it("does not preview an unsaved sample row", async () => {
  shown();
  mount(true);
  await act(async () => {});
  expect(screen.getByTestId("reads-eval-pending")).toBeInTheDocument();
  expect(http.post).not.toHaveBeenCalled();
});

it("does not let the mapping-card claim enable the saved-input table", async () => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: ["exp.new.readsTable"],
  });
  mount();
  await act(async () => {});
  expect(screen.queryByTestId("reads-on-sample")).toBeNull();
  expect(http.post).not.toHaveBeenCalled();
});

it("hides composite previews without issuing unsupported requests", async () => {
  shown();
  mount(false, [{ ...evaluation, templateType: "composite" }]);
  await act(async () => {});
  expect(screen.queryByTestId("reads-on-sample")).toBeNull();
  expect(http.post).not.toHaveBeenCalled();
});

it("only previews single evaluations in a mixed selection", async () => {
  shown();
  mount(false, [
    { ...evaluation, templateId: "composite-id", templateType: "composite" },
    evaluation,
  ]);
  await waitFor(() => expect(screen.getByText("= 0")).toBeInTheDocument());
  expect(screen.getAllByTestId("reads-eval")).toHaveLength(1);
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post.mock.calls[0][1].template_id).toBe("template-id");
});

it("reports a preview error without falling back to the execution endpoint", async () => {
  shown();
  http.post.mockRejectedValue(new Error("Unavailable"));
  mount();
  await waitFor(() =>
    expect(
      screen.getByText("Could not read what this evaluation reads on this row"),
    ).toBeInTheDocument(),
  );
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post.mock.calls[0][0]).toBe(EVAL_MAPPING_PREVIEW);
});
