import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";

import { SnackbarProvider } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import AlertSettingsForm from "../components/AlertSettingsForm";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// get_eval_names lists only the evals that have already produced results. On
// a project with no eval task, or with a task that has not run yet, it returns
// [] and a new Evaluation Metrics alert has nothing to pick. The form has to
// say why and point to where eval tasks are set up, instead of a bare
// "No option provided" and a Create that fails on "Metric is required".

const PROJECT_ID = "project-1";

const GROUNDEDNESS = {
  id: "eval-1",
  name: "Groundedness",
  output_type: "Pass/Fail",
  choices: ["Passed", "Failed"],
};

const mockEvalNames = (respond) =>
  vi
    .spyOn(axios, "get")
    .mockImplementation((url) =>
      url === endpoints.project.getTraceEvals()
        ? respond()
        : Promise.resolve({ data: { result: [] } }),
    );

const renderNewEvalAlert = () => {
  useAlertStore.setState({
    openSheetView: null,
    selectedProject: PROJECT_ID,
    alertType: "evaluation_metrics",
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
};

const evalNamesRequested = () =>
  waitFor(() =>
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.project.getTraceEvals(),
      expect.objectContaining({ params: { project_id: PROJECT_ID } }),
    ),
  );

// Two pickers are labelled "Metric": the metric type, and for eval alerts the
// eval itself, which is required so its label reads "Metric *".
const evalPicker = () =>
  screen
    .getAllByLabelText(/^Metric/)
    .find((input) => input.getAttribute("data-alert-field") !== "metric-type");

// An open picker menu hides the rest of the page from the accessibility tree,
// so look for the link with hidden elements included.
const evalTaskLink = () =>
  screen.queryByRole("link", { name: /eval task/i, hidden: true });

describe("Evaluation Metrics alert on a project whose evals have no results yet", () => {
  beforeEach(() => {
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  it("explains the empty eval list and links to eval task setup for this project", async () => {
    mockEvalNames(() => Promise.resolve({ data: { result: [] } }));
    renderNewEvalAlert();

    const link = await screen.findByRole("link", {
      name: /set up an eval task/i,
    });
    expect(link).toHaveAttribute(
      "href",
      `/dashboard/tasks/create?project=${PROJECT_ID}`,
    );
    expect(
      screen.getByText(/No evals have results in this project yet/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/show up here after the first run/),
    ).toBeInTheDocument();
  });

  it("names the reason inside the empty Metric picker instead of 'No option provided'", async () => {
    mockEvalNames(() => Promise.resolve({ data: { result: [] } }));
    renderNewEvalAlert();
    await evalNamesRequested();

    fireEvent.click(evalPicker());

    expect(
      await screen.findByRole("menuitem", {
        name: "No evals with results yet",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: "No option provided" }),
    ).toBeNull();
  });

  it("says the eval list is loading while it is fetched, with no empty-state hint", async () => {
    mockEvalNames(() => new Promise(() => {}));
    renderNewEvalAlert();
    await evalNamesRequested();

    expect(evalTaskLink()).toBeNull();
    expect(screen.queryByText(/No evals have results/)).toBeNull();

    fireEvent.click(evalPicker());

    expect(
      await screen.findByRole("menuitem", { name: "Loading evals..." }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: "No evals with results yet" }),
    ).toBeNull();
  });

  it("says the eval list failed to load, with no empty-state hint", async () => {
    mockEvalNames(() => Promise.reject(new Error("get_eval_names failed")));
    renderNewEvalAlert();
    await evalNamesRequested();

    fireEvent.click(evalPicker());

    expect(
      await screen.findByRole("menuitem", { name: "Could not load evals" }),
    ).toBeInTheDocument();
    expect(evalTaskLink()).toBeNull();
    expect(screen.queryByText(/No evals have results/)).toBeNull();
  });

  it("shows no hint or link once the project has evals with results", async () => {
    mockEvalNames(() => Promise.resolve({ data: { result: [GROUNDEDNESS] } }));
    renderNewEvalAlert();
    await evalNamesRequested();

    fireEvent.click(evalPicker());

    expect(
      await screen.findByRole("menuitem", { name: GROUNDEDNESS.name }),
    ).toBeInTheDocument();
    expect(evalTaskLink()).toBeNull();
    expect(screen.queryByText(/No evals have results/)).toBeNull();
  });
});
