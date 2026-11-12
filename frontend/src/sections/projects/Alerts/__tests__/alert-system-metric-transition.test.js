import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { SnackbarProvider } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { normalizeAlertDetail } from "../common";
import AlertSettingsForm from "../components/AlertSettingsForm";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

const evaluationCases = [
  {
    name: "choices",
    evaluation: {
      id: "eval-choices",
      name: "Classification",
      output_type: "choices",
      choices: ["Good", "Poor"],
    },
    choice: "Good",
    targetLabel: "Count of errors",
    targetMetric: "count_of_errors",
  },
  {
    name: "pass-fail",
    evaluation: {
      id: "eval-pass-fail",
      name: "Groundedness",
      output_type: "Pass/Fail",
      choices: ["Passed", "Failed"],
    },
    choice: "Passed",
    targetLabel: "Span response time",
    targetMetric: "span_response_time",
  },
  {
    name: "score",
    evaluation: {
      id: "eval-score",
      name: "Coherence",
      output_type: "score",
      choices: null,
    },
    choice: "",
    targetLabel: "Token usage",
    targetMetric: "token_usage",
  },
];

let queryClient;

const openEvaluationAlert = async ({ evaluation, choice }) => {
  const detail = {
    id: "alert-transition",
    name: "Evaluation alert",
    project: "project-transition",
    metric_type: "evaluation_metrics",
    metric: evaluation.id,
    metric_name: evaluation.name,
    threshold_metric_value: choice,
    threshold_type: "static",
    threshold_operator: "greater_than",
    critical_threshold_value: 0.4,
    warning_threshold_value: 0.3,
    alert_frequency: 60,
    filters: {},
    notification_emails: ["alerts@example.test"],
    slack_webhook_url: "",
    slack_notes: "",
  };

  vi.spyOn(axios, "get").mockImplementation((url) =>
    Promise.resolve({
      data: {
        result: url === endpoints.project.getTraceEvals() ? [evaluation] : [],
      },
    }),
  );
  const patch = vi
    .spyOn(axios, "patch")
    .mockResolvedValue({ data: { result: "ok" } });
  const post = vi
    .spyOn(axios, "post")
    .mockResolvedValue({ data: { result: "ok" } });

  useAlertStore.setState({
    openSheetView: detail.id,
    selectedProject: detail.project,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  renderWithRouter(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(
        SnackbarProvider,
        null,
        React.createElement(AlertSettingsForm, {
          onThresholdTypeChange: vi.fn(),
          setThresholdOperator: vi.fn(),
          setWarningValue: vi.fn(),
          setCriticalValue: vi.fn(),
          setFormIsDirty: vi.fn(),
          onPayloadChange: vi.fn(),
        }),
      ),
    ),
  );
  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(detail),
    });
  });
  await waitFor(() =>
    expect(screen.getByDisplayValue(evaluation.name)).toBeInTheDocument(),
  );
  return { detail, patch, post };
};

const submitUpdate = async ({ detail, patch, post }) => {
  await act(async () => {
    fireEvent.click(
      document.querySelector('[data-alert-form-submit="update"]'),
    );
  });
  await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
  expect(patch.mock.calls[0][0]).toBe(
    `${endpoints.project.createMonitor}${detail.id}/`,
  );
  expect(post).not.toHaveBeenCalled();
  // Check the wire payload: undefined would be omitted, not clear a PATCH field.
  return JSON.parse(JSON.stringify(patch.mock.calls[0][1]));
};

describe("AlertSettingsForm system metric transitions", () => {
  beforeEach(() => {
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  it.each(evaluationCases)(
    "clears evaluation fields when changing $name to $targetMetric",
    async (testCase) => {
      const context = await openEvaluationAlert(testCase);
      fireEvent.click(
        document.querySelector('[data-alert-field="metric-type"]'),
      );
      const option = await screen.findByRole("menuitem", {
        name: testCase.targetLabel,
      });
      fireEvent.click(within(option).getByText(testCase.targetLabel));
      await waitFor(() =>
        expect(
          document.querySelector('[data-alert-field="metric-type"]'),
        ).toHaveValue(testCase.targetLabel),
      );
      fireEvent.change(
        document.querySelector('[data-alert-field="critical-threshold-value"]'),
        { target: { value: "10" } },
      );
      fireEvent.change(
        document.querySelector('[data-alert-field="warning-threshold-value"]'),
        { target: { value: "5" } },
      );

      const body = await submitUpdate(context);
      expect(body).toMatchObject({
        name: context.detail.name,
        project: context.detail.project,
        metric_type: testCase.targetMetric,
        metric: null,
        threshold_metric_value: null,
        threshold_type: "static",
        threshold_operator: "greater_than",
        critical_threshold_value: 10,
        warning_threshold_value: 5,
        notification_emails: ["alerts@example.test"],
      });
    },
  );

  it.each(evaluationCases)(
    "preserves evaluation fields when saving an unchanged $name metric",
    async (testCase) => {
      const context = await openEvaluationAlert(testCase);
      const body = await submitUpdate(context);
      expect(body.metric_type).toBe("evaluation_metrics");
      expect(body.metric).toBe(testCase.evaluation.id);
      if (testCase.choice) {
        expect(body.threshold_metric_value).toBe(testCase.choice);
      } else {
        expect(body).not.toHaveProperty("threshold_metric_value");
      }
    },
  );
});
