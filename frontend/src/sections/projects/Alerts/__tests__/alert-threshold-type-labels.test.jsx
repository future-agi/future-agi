import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderWithRouter, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import { alertDefinitionOptions } from "../common";
import AlertSettingsForm from "../components/AlertSettingsForm";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// The two ways to define an alert read as sentences a person can follow, not
// as templates with a placeholder in braces. The threshold numbers are typed
// below the choice, one for critical and one for warning, so each sentence
// names what the number does rather than one value.
const STATIC_LABEL =
  "Static value: alert when the metric goes above or below a number you set";
const PERCENTAGE_LABEL =
  "Percentage change: alert when the metric rises or falls by more than a percentage you set, compared to the previous period";

// The form's Iconify glyphs: the pickers' arrow and clear buttons, and the
// filter bar's add button.
for (const name of [
  "solar:alt-arrow-up-linear",
  "solar:close-linear",
  "solar:add-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

let queryClient;

// The text beside one of the radios that pick how the alert is defined.
const radioLabel = (value) => {
  const radio = document.querySelector(
    `input[name="threshold_type"][value="${value}"]`,
  );
  return radio ? radio.closest("label").textContent : null;
};

const renderNewAlert = () => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  useAlertStore.setState({
    openSheetView: null,
    selectedProject: "project-new",
    alertType: "count_of_errors",
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
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
};

describe("the choice of how an alert is defined", () => {
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

  it("reads each choice on the form as a sentence with no placeholder", async () => {
    renderNewAlert();

    await waitFor(() => expect(radioLabel("static")).not.toBeNull());
    const labels = [radioLabel("static"), radioLabel("percentage_change")];

    expect(labels).toEqual([STATIC_LABEL, PERCENTAGE_LABEL]);
    for (const label of labels) expect(label).not.toMatch(/[{}]/);
  });

  it("gives every choice the form offers a label with no braces", () => {
    for (const { label } of alertDefinitionOptions) {
      expect(label).not.toMatch(/[{}]/);
    }
  });

  it("keeps the same two choices, static first", () => {
    expect(alertDefinitionOptions.map(({ value }) => value)).toEqual([
      "static",
      "percentage_change",
    ]);
  });
});
