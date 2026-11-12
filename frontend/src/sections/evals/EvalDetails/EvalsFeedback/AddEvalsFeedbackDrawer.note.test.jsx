/* eslint-disable react/prop-types */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// W10-EVALS-U08 / W11-EVALS-FB-NOTE-FIELD: Edit Feedback starts from the note
// the person wrote (Observe keeps it in `feedback_improvement`, not in the
// eval's own `explanation`), and its read-only Explanation shows only the
// eval's explanation, never the person's note.

const TRACE_ACTION_URL =
  "/tracer/observation-span/submit_feedback_action_type/";
const NOTE_PLACEHOLDER =
  "Write the explanation the eval should have given for this result, and why it's correct";
const EVAL_EXPLANATION =
  "The response cites a refund policy that does not exist";
const OBSERVE_NOTE = "The policy exists; it is on the pricing page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mocks.enqueueSnackbar(...args),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin", user: { id: "user-me" } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <span>{text}</span>,
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mocks.get(...args),
    post: (...args) => mocks.post(...args),
  },
  endpoints: {
    develop: {
      eval: {
        getEvalConfigs: "/model-hub/get-eval-configs/",
        updateFeedback: "/model-hub/feedback/submit-feedback/",
        addEvalsFeedback: "/model-hub/eval-playground/feedback/",
      },
      experiment: {
        feedback: {
          submit: (id) =>
            `/model-hub/experiments/v2/${id}/feedback/submit-feedback/`,
        },
      },
    },
    project: {
      submitObservationSpanFeedbackActionType: () => TRACE_ACTION_URL,
    },
  },
}));

import AddEvalsFeedbackDrawer from "./AddEvalsFeedbackDrawer";

const observeRow = {
  id: "fb-observe",
  source: "observe",
  source_id: "span-observe",
  value: "failed",
  explanation: EVAL_EXPLANATION,
  feedback_improvement: OBSERVE_NOTE,
  action_type: "retune",
  user_id: "user-me",
  user_eval_metric_id: "",
  custom_eval_config_id: "cfg-observe",
  experiment_id: "",
};

const datasetRow = {
  id: "fb-dataset",
  source: "dataset",
  source_id: "column-1",
  value: "passed",
  explanation: "Dataset note",
  feedback_improvement: "",
  action_type: "retune",
  user_id: "user-me",
  user_eval_metric_id: "metric-1",
  custom_eval_config_id: "",
  experiment_id: "",
};

let queryClient;

function renderDrawer(existingFeedback, reason) {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <AddEvalsFeedbackDrawer
        open
        onClose={vi.fn()}
        evalsId="template-1"
        output={{ reason }}
        selectedAddFeedback={{ id: existingFeedback.source_id }}
        existingFeedback={existingFeedback}
      />
    </QueryClientProvider>,
  );
}

describe("Edit Feedback note", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({
      data: { result: { eval: { output: "Pass/Fail" } } },
    });
    mocks.post.mockResolvedValue({
      data: { result: { message: "Action type submitted successfully" } },
    });
  });

  afterEach(() => {
    cleanup();
    queryClient?.clear();
  });

  it("starts an Observe edit from the person's note, not the eval's explanation", async () => {
    renderDrawer(observeRow, EVAL_EXPLANATION);

    await screen.findByRole("radio", { name: "Failed" });
    await waitFor(() =>
      expect(screen.getByPlaceholderText(NOTE_PLACEHOLDER)).toHaveValue(
        OBSERVE_NOTE,
      ),
    );
    // The read-only Explanation is the eval's own.
    expect(screen.getByText("Explanation")).toBeInTheDocument();
    expect(screen.getByText(EVAL_EXPLANATION)).toBeInTheDocument();
  });

  it("saves the person's note unchanged when only the value changes", async () => {
    renderDrawer(observeRow, EVAL_EXPLANATION);

    const passed = await screen.findByRole("radio", { name: "Passed" });
    await waitFor(() =>
      expect(screen.getByPlaceholderText(NOTE_PLACEHOLDER)).toHaveValue(
        OBSERVE_NOTE,
      ),
    );
    fireEvent.click(passed);
    fireEvent.click(screen.getByRole("radio", { name: /^Re-tune/ }));
    const save = screen.getByRole("button", { name: "Save changes" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(TRACE_ACTION_URL, {
        action_type: "retune",
        feedback_id: "fb-observe",
        observation_span_id: "span-observe",
        custom_eval_config_id: "cfg-observe",
        value: "passed",
        explanation: OBSERVE_NOTE,
      }),
    );
  });

  it("starts a dataset edit from the note saved on it", async () => {
    renderDrawer(datasetRow, "");

    await screen.findByRole("radio", { name: "Failed" });
    await waitFor(() =>
      expect(screen.getByPlaceholderText(NOTE_PLACEHOLDER)).toHaveValue(
        "Dataset note",
      ),
    );
  });

  it("leaves out the Explanation when the eval's explanation isn't known", async () => {
    renderDrawer(datasetRow, "");

    await screen.findByRole("radio", { name: "Failed" });
    expect(screen.queryByText("Explanation")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Unable to fetch Explanation"),
    ).not.toBeInTheDocument();
  });
});
