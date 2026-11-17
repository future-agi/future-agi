import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AddEvalsFeedbackDrawer from "./AddEvalsFeedbackDrawer";

const state = vi.hoisted(() => ({
  config: { output: "Pass/Fail" },
  submitFeedback: vi.fn(),
  renderForm: vi.fn(() => null),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: state.config, isPending: false }),
  useMutation: () => ({ mutate: state.submitFeedback, isPending: false }),
}));
vi.mock("src/utils/axios", () => ({
  default: {},
  endpoints: {
    project: {
      submitObservationSpanFeedbackActionType: () =>
        "/observe-feedback-action/",
    },
    develop: { eval: { updateFeedback: "/dataset-feedback/" } },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/hooks/use-drawer-stack", () => ({
  useDrawerCovered: () => false,
  coveredDrawerSx: () => ({}),
}));
vi.mock("./AddEvalsFeedbackForm", () => ({
  default: (props) => state.renderForm(props),
}));

const existingFeedback = {
  id: "feedback-1",
  source: "observe",
  source_id: "span-1",
  custom_eval_config_id: "config-1",
  value: "passed",
  explanation: "Original explanation",
  action_type: "retune",
};

function submitCorrection(payload, feedback = existingFeedback) {
  render(
    <AddEvalsFeedbackDrawer
      open
      onClose={vi.fn()}
      refreshGrid={vi.fn()}
      output={{ reason: "Evaluation explanation" }}
      evalsId="eval-1"
      existingFeedback={feedback}
    />,
  );
  const form = state.renderForm.mock.lastCall[0];
  act(() => form.handleSubmitForm(payload));
}

beforeEach(() => {
  vi.clearAllMocks();
  state.config = { output: "Pass/Fail" };
});
afterEach(cleanup);

describe.each(["retune", "recalculate"])(
  "observe feedback %s",
  (actionType) => {
    it.each([
      { output: "Pass/Fail", value: "failed", savedValue: "failed" },
      { output: "score", value: 0, savedValue: 0 },
      {
        output: "choices",
        multiChoice: true,
        value: ["relevant", "complete"],
        savedValue: '["relevant","complete"]',
      },
    ])("sends the edited $output value and explanation", (testCase) => {
      state.config = {
        output: testCase.output,
        multi_choice: testCase.multiChoice || false,
      };
      submitCorrection({
        actionType,
        value: testCase.value,
        explanation: "Corrected explanation",
      });

      expect(state.submitFeedback).toHaveBeenCalledExactlyOnceWith({
        url: "/observe-feedback-action/",
        body: {
          action_type: actionType,
          feedback_id: "feedback-1",
          observation_span_id: "span-1",
          custom_eval_config_id: "config-1",
          value: testCase.savedValue,
          explanation: "Corrected explanation",
        },
      });
    });
  },
);

it("keeps dataset edits and their recalculation action unchanged", () => {
  submitCorrection(
    {
      actionType: "recalculate",
      value: "failed",
      explanation: "Corrected explanation",
    },
    { ...existingFeedback, source: "dataset", user_eval_metric_id: "metric-1" },
  );

  expect(state.submitFeedback).toHaveBeenCalledExactlyOnceWith({
    url: "/dataset-feedback/",
    body: {
      action_type: "retune_recalculate",
      feedback_id: "feedback-1",
      user_eval_metric_id: "metric-1",
      value: "failed",
      explanation: "Corrected explanation",
    },
  });
});
