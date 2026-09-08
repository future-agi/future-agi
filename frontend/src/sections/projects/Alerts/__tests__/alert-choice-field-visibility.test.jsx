import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter, screen, waitFor } from "src/utils/test-utils";

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
});
