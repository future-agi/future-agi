import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";

import { SnackbarProvider } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { normalizeAlertDetail } from "../common";
import AlertSettingsForm from "../components/AlertSettingsForm";
import { savedAlert as baseSavedAlert } from "./fixtures";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// A "Pass/Fail" or "choices" eval alerts on the share of rows with a given
// label, so the form must offer a Choice picker. A "score" eval alerts on
// the mean score instead, even when it happens to carry `choices` — the
// picker must not appear for it.
const cases = [
  {
    name: "Pass/Fail eval",
    evaluation: {
      id: "eval-1",
      name: "Groundedness",
      output_type: "Pass/Fail",
      choices: ["Passed", "Failed"],
    },
    thresholdMetricValue: "Passed",
    expectChoice: true,
  },
  {
    name: "choices eval",
    evaluation: {
      id: "eval-2",
      name: "Politeness",
      output_type: "choices",
      choices: ["never", "occasionally", "frequently", "always"],
    },
    thresholdMetricValue: "never",
    expectChoice: true,
  },
  {
    name: "score eval with no choices",
    evaluation: {
      id: "eval-3",
      name: "Coherence",
      output_type: "score",
      choices: null,
    },
    thresholdMetricValue: "",
    expectChoice: false,
  },
  {
    name: "score eval that still carries choices",
    evaluation: {
      id: "eval-4",
      name: "Helpfulness",
      output_type: "score",
      choices: ["Complete", "Partial", "Incomplete"],
    },
    thresholdMetricValue: "",
    expectChoice: false,
  },
];

const openAlertForEditing = async (evaluation, thresholdMetricValue) => {
  const detail = {
    ...baseSavedAlert,
    metric: evaluation.id,
    metric_name: evaluation.name,
    threshold_type: "percentage_change",
    threshold_metric_value: thresholdMetricValue,
    critical_threshold_value: 0,
    filters: {},
  };

  vi.spyOn(axios, "get").mockImplementation((url) =>
    Promise.resolve({
      data: {
        result: url === endpoints.project.getTraceEvals() ? [evaluation] : [],
      },
    }),
  );

  useAlertStore.setState({
    openSheetView: detail.id,
    selectedProject: detail.project,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });

  renderWithRouter(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SnackbarProvider>
        <AlertSettingsForm
          onThresholdTypeChange={vi.fn()}
          setThresholdOperator={vi.fn()}
          setWarningValue={vi.fn()}
          setCriticalValue={vi.fn()}
          setFormIsDirty={vi.fn()}
          onPayloadChange={vi.fn()}
        />
      </SnackbarProvider>
    </QueryClientProvider>,
  );

  // The sheet mounts before the alert request resolves, so the saved alert
  // reaches the form only after the form already exists.
  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(detail),
    });
  });

  // Wait for the evaluations list to load and the metric field to hydrate
  // with the matching eval's name — only then has selectedMetricOptions had
  // a chance to be computed from the real (loaded) evaluation shape.
  await waitFor(() =>
    expect(screen.getByDisplayValue(evaluation.name)).toBeInTheDocument(),
  );
};

describe("Choice field visibility depends on the eval's output type", () => {
  beforeEach(() => {
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  it.each(cases)(
    "$name: Choice label is $expectChoice",
    async ({ evaluation, thresholdMetricValue, expectChoice }) => {
      await openAlertForEditing(evaluation, thresholdMetricValue);

      if (expectChoice) {
        expect(screen.getAllByLabelText("Choice").length).toBeGreaterThan(0);
      } else {
        expect(screen.queryAllByLabelText("Choice")).toHaveLength(0);
      }
    },
  );

  it("does not resend a hydrated stale choice for a score eval on submit", async () => {
    const scoreEvalWithLabels = {
      id: "eval-4",
      name: "Helpfulness",
      output_type: "score",
      choices: ["Complete", "Partial", "Incomplete"],
    };

    await openAlertForEditing(scoreEvalWithLabels, "Incomplete");

    const patch = vi
      .spyOn(axios, "patch")
      .mockResolvedValue({ data: { result: "ok" } });

    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-alert-form-submit="update"]'),
      );
    });

    await waitFor(() => expect(patch).toHaveBeenCalled());
    const body = patch.mock.calls.at(-1)[1];
    expect(body).not.toHaveProperty("threshold_metric_value");
    expect(body).toHaveProperty("metric", scoreEvalWithLabels.id);
  });

  it("keeps a saved choice when the eval picker has not resolved the eval", async () => {
    // baseSavedAlert is a Pass/Fail eval hydrated with threshold_metric_value
    // "Passed". The picker mock resolves to [] here, the same shape it has
    // while the get_eval_names query is in flight, 503s, or the eval simply
    // has no ClickHouse rows yet — selectedEval is then undefined, and the
    // saved choice must not be dropped from the submit payload on that basis
    // alone.
    const detail = {
      ...baseSavedAlert,
      threshold_type: "percentage_change",
      critical_threshold_value: 0,
      filters: {},
    };

    vi.spyOn(axios, "get").mockImplementation(() =>
      Promise.resolve({ data: { result: [] } }),
    );

    useAlertStore.setState({
      openSheetView: detail.id,
      selectedProject: detail.project,
    });
    useAlertSheetStore.setState({
      alertRuleDetails: null,
      gridRef: { current: null },
    });

    renderWithRouter(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <SnackbarProvider>
          <AlertSettingsForm
            onThresholdTypeChange={vi.fn()}
            setThresholdOperator={vi.fn()}
            setWarningValue={vi.fn()}
            setCriticalValue={vi.fn()}
            setFormIsDirty={vi.fn()}
            onPayloadChange={vi.fn()}
          />
        </SnackbarProvider>
      </QueryClientProvider>,
    );

    act(() => {
      useAlertSheetStore.setState({
        alertRuleDetails: normalizeAlertDetail(detail),
      });
    });

    // The picker resolved to [] so there is no option to resolve the eval's
    // name from — the metric field falls back to displaying the raw id.
    // That is the signal that hydration has settled with an unresolved eval.
    await waitFor(() =>
      expect(screen.getByDisplayValue(detail.metric)).toBeInTheDocument(),
    );

    const patch = vi
      .spyOn(axios, "patch")
      .mockResolvedValue({ data: { result: "ok" } });

    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-alert-form-submit="update"]'),
      );
    });

    await waitFor(() => expect(patch).toHaveBeenCalled());
    const body = patch.mock.calls.at(-1)[1];
    expect(body).toHaveProperty(
      "threshold_metric_value",
      detail.threshold_metric_value,
    );
    expect(body).toHaveProperty("metric", detail.metric);
  });
});

// The alert preview graph is enabled through onPayloadChange's second argument.
// A "Pass/Fail" or "choices" eval's graph endpoint requires the chosen label,
// so the preview must not be enabled until a choice is set — otherwise it fires
// and the backend returns 400. A "score" eval needs no choice and previews as
// soon as the metric is chosen.
describe("Choice-thresholded evals gate the preview graph on a chosen label", () => {
  const CHOICES_EVAL = {
    id: "eval-2",
    name: "Politeness",
    output_type: "choices",
    choices: ["never", "occasionally", "frequently", "always"],
  };
  const SCORE_EVAL = {
    id: "eval-3",
    name: "Coherence",
    output_type: "score",
    choices: null,
  };

  const renderDirtiedAlert = async (evaluation, thresholdMetricValue) => {
    // savedAlert carries valid thresholds (less_than, 5 < 12), so the only
    // variable under test is whether a choice is present.
    const detail = {
      ...baseSavedAlert,
      metric: evaluation.id,
      metric_name: evaluation.name,
      threshold_metric_value: thresholdMetricValue,
      filters: {},
    };

    vi.spyOn(axios, "get").mockImplementation((url) =>
      Promise.resolve({
        data: {
          result: url === endpoints.project.getTraceEvals() ? [evaluation] : [],
        },
      }),
    );

    useAlertStore.setState({
      openSheetView: detail.id,
      selectedProject: detail.project,
    });
    useAlertSheetStore.setState({
      alertRuleDetails: null,
      gridRef: { current: null },
    });

    const payloadCalls = [];
    const onPayloadChange = vi.fn((payload, enabled) =>
      payloadCalls.push({ payload, enabled }),
    );

    renderWithRouter(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <SnackbarProvider>
          <AlertSettingsForm
            onThresholdTypeChange={vi.fn()}
            setThresholdOperator={vi.fn()}
            setWarningValue={vi.fn()}
            setCriticalValue={vi.fn()}
            setFormIsDirty={vi.fn()}
            onPayloadChange={onPayloadChange}
          />
        </SnackbarProvider>
      </QueryClientProvider>,
    );

    act(() => {
      useAlertSheetStore.setState({
        alertRuleDetails: normalizeAlertDetail(detail),
      });
    });

    await waitFor(() =>
      expect(screen.getByDisplayValue(evaluation.name)).toBeInTheDocument(),
    );

    // isQueryEnabled requires the edited form to be dirty; a name edit dirties
    // it without touching the choice under test.
    const nameInput = document.querySelector('[data-alert-field="name"]');
    act(() => {
      fireEvent.change(nameInput, { target: { value: "Edited alert name" } });
    });

    return payloadCalls;
  };

  const lastEnabled = (calls) =>
    calls.length ? calls[calls.length - 1].enabled : undefined;

  it("enables the preview once a choices eval has a chosen label (edit mode)", async () => {
    const calls = await renderDirtiedAlert(CHOICES_EVAL, "frequently");
    await waitFor(() => expect(lastEnabled(calls)).toBe(true));
  });

  it("keeps the preview disabled for a choices eval with no chosen label", async () => {
    const calls = await renderDirtiedAlert(CHOICES_EVAL, "");
    // Every gating field is debounced 300ms; wait comfortably past that so a
    // would-be enable (the pre-fix behaviour, which enabled as soon as the
    // metric was set) has had time to fire before we assert it never did.
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(calls.some(({ enabled }) => enabled === true)).toBe(false);
  });

  it("enables the preview for a score eval without a choice (no regression)", async () => {
    const calls = await renderDirtiedAlert(SCORE_EVAL, "");
    await waitFor(() => expect(lastEnabled(calls)).toBe(true));
  });
});

// Saving an edit changes the alert's server-side graph, but the details-page
// graph query is keyed only on the alert id + date window, so its cache goes
// stale after a threshold_type/config change. The update must invalidate
// ["alert-graph"] so the saved graph refetches immediately instead of waiting
// on the 10s poll.
describe("Saving an alert refreshes the details-page graph", () => {
  it("invalidates the alert-graph query on update success", async () => {
    const evaluation = {
      id: "eval-1",
      name: "Groundedness",
      output_type: "Pass/Fail",
      choices: ["Passed", "Failed"],
    };
    const detail = {
      ...baseSavedAlert,
      metric: evaluation.id,
      metric_name: evaluation.name,
      threshold_metric_value: "Passed",
      filters: {},
    };

    vi.spyOn(axios, "get").mockImplementation((url) =>
      Promise.resolve({
        data: {
          result: url === endpoints.project.getTraceEvals() ? [evaluation] : [],
        },
      }),
    );

    useAlertStore.setState({
      openSheetView: detail.id,
      selectedProject: detail.project,
    });
    useAlertSheetStore.setState({
      alertRuleDetails: null,
      gridRef: { current: null },
    });

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    renderWithRouter(
      <QueryClientProvider client={queryClient}>
        <SnackbarProvider>
          <AlertSettingsForm
            onThresholdTypeChange={vi.fn()}
            setThresholdOperator={vi.fn()}
            setWarningValue={vi.fn()}
            setCriticalValue={vi.fn()}
            setFormIsDirty={vi.fn()}
            onPayloadChange={vi.fn()}
          />
        </SnackbarProvider>
      </QueryClientProvider>,
    );

    act(() => {
      useAlertSheetStore.setState({
        alertRuleDetails: normalizeAlertDetail(detail),
      });
    });

    await waitFor(() =>
      expect(screen.getByDisplayValue(evaluation.name)).toBeInTheDocument(),
    );

    const patch = vi
      .spyOn(axios, "patch")
      .mockResolvedValue({ data: { result: "ok" } });

    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-alert-form-submit="update"]'),
      );
    });

    await waitFor(() => expect(patch).toHaveBeenCalled());
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["alert-graph"] }),
    );
  });
});
