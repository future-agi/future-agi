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

// W10-EVALS-U01: Edit Feedback on a row left on a trace (Observe, or a
// prototype run) saves the new value and note through the tracer, along with
// the follow-up action (W10-EVALS-L04). Only the person who left the feedback
// can save it, and the drawer says so before they fill in the form.

const TRACE_ACTION_URL =
  "/tracer/observation-span/submit_feedback_action_type/";
const DATASET_EDIT_URL = "/model-hub/feedback/submit-feedback/";
const OWNER_ONLY = "Only the person who left this feedback can edit it.";
const NOTE_PLACEHOLDER =
  "Write the explanation the eval should have given for this result, and why it's correct";

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
  value: "passed",
  explanation: "",
  action_type: "retune",
  user_id: "user-me",
  user_eval_metric_id: "",
  custom_eval_config_id: "cfg-observe",
  experiment_id: "",
};

const prototypeRow = {
  id: "fb-prototype",
  source: "experiment",
  source_id: "span-prototype",
  value: "failed",
  explanation: "Old note",
  action_type: "retune",
  user_id: "user-me",
  user_eval_metric_id: "",
  custom_eval_config_id: "cfg-prototype",
  experiment_id: "",
};

const teammateObserveRow = {
  ...observeRow,
  id: "fb-observe-teammate",
  source_id: "span-teammate",
  explanation: "Teammate note",
  user_id: "user-teammate",
};

const datasetRow = {
  id: "fb-dataset",
  source: "dataset",
  source_id: "column-1",
  value: "passed",
  explanation: "Old note",
  action_type: "retune",
  user_id: "someone-else",
  user_eval_metric_id: "metric-1",
  custom_eval_config_id: "",
  experiment_id: "",
};

let queryClient;

function renderDrawer(existingFeedback) {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <AddEvalsFeedbackDrawer
        open
        onClose={onClose}
        evalsId="template-1"
        output={{ reason: existingFeedback.explanation }}
        selectedAddFeedback={{ id: existingFeedback.source_id }}
        existingFeedback={existingFeedback}
      />
    </QueryClientProvider>,
  );
  return { onClose };
}

function writeNote(text) {
  fireEvent.change(screen.getByPlaceholderText(NOTE_PLACEHOLDER), {
    target: { value: text },
  });
}

describe("Edit Feedback for feedback left on a trace", () => {
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

  it("saves the new value, note and follow-up action of an Observe row", async () => {
    const { onClose } = renderDrawer(observeRow);

    const failed = await screen.findByRole("radio", { name: "Failed" });
    expect(screen.queryByText(OWNER_ONLY)).not.toBeInTheDocument();
    expect(failed).toBeEnabled();
    expect(screen.getByPlaceholderText(NOTE_PLACEHOLDER)).toBeEnabled();

    fireEvent.click(failed);
    writeNote("Corrected note");
    fireEvent.click(
      screen.getByRole("radio", { name: /^Re-calculate and re-tune/ }),
    );
    const save = screen.getByRole("button", { name: "Save changes" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(TRACE_ACTION_URL, {
        action_type: "recalculate",
        feedback_id: "fb-observe",
        observation_span_id: "span-observe",
        custom_eval_config_id: "cfg-observe",
        value: "failed",
        explanation: "Corrected note",
      }),
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("Changes saved", {
        variant: "success",
      }),
    );
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Action type submitted successfully",
      expect.anything(),
    );
    expect(onClose).toHaveBeenCalledWith(true);
  });

  it("saves the new value and note of a prototype-run row through the tracer instead of failing on submit", async () => {
    renderDrawer(prototypeRow);

    const passed = await screen.findByRole("radio", { name: "Passed" });
    expect(screen.queryByText(OWNER_ONLY)).not.toBeInTheDocument();
    expect(passed).toBeEnabled();

    fireEvent.click(passed);
    writeNote("New note");
    const save = screen.getByRole("button", { name: "Save changes" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(TRACE_ACTION_URL, {
        action_type: "retune",
        feedback_id: "fb-prototype",
        observation_span_id: "span-prototype",
        custom_eval_config_id: "cfg-prototype",
        value: "passed",
        explanation: "New note",
      }),
    );
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      expect.stringContaining("experiment id missing"),
      expect.anything(),
    );
    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("Changes saved", {
        variant: "success",
      }),
    );
  });

  it("tells a teammate only the author can edit Observe feedback and sends nothing", async () => {
    const { onClose } = renderDrawer(teammateObserveRow);

    expect(await screen.findByText(OWNER_ONLY)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Failed" }));
    writeNote("Teammate correction");
    fireEvent.click(
      screen.getByRole("radio", { name: /^Re-calculate and re-tune/ }),
    );
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    fireEvent.click(save);

    expect(mocks.post).not.toHaveBeenCalled();
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalledWith(true);
  });

  it("keeps a teammate's dataset row editable and sends its value and note", async () => {
    renderDrawer(datasetRow);

    const failed = await screen.findByRole("radio", { name: "Failed" });
    expect(screen.queryByText(OWNER_ONLY)).not.toBeInTheDocument();
    expect(failed).toBeEnabled();

    fireEvent.click(failed);
    writeNote("New note");
    const save = screen.getByRole("button", { name: "Save changes" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(DATASET_EDIT_URL, {
        action_type: "retune",
        feedback_id: "fb-dataset",
        user_eval_metric_id: "metric-1",
        value: "failed",
        explanation: "New note",
      }),
    );
    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("Changes saved", {
        variant: "success",
      }),
    );
  });
});
