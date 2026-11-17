import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, renderWithRouter, screen, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import ThresholdPreview from "./ThresholdPreview";
import AlertSettingsForm from "./AlertSettingsForm";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import { resetAlertSheetStoreState, useAlertSheetStore } from "../store/useAlertSheetStore";
import { normalizeAlertDetail } from "../common";

vi.mock("./AlertsChart", () => ({
  default: ({ series }) => <div data-testid="threshold-series">{JSON.stringify(series)}</div>,
}));

const definition = {
  name: "Error count", project: "project-1", metric_type: "count_of_errors",
  threshold_type: "static", threshold_operator: "greater_than",
  critical_threshold_value: 5, warning_threshold_value: 2, alert_frequency: 60,
  auto_threshold_time_window: 1440,
  filters: { observation_type: ["llm"] },
};
const result = {
  fire_count: 1, bucket_count: 2, window_start: "2026-11-09T12:00:00Z",
  window_end: "2026-11-16T12:00:00Z", window_limited: false,
  series: [
    { timestamp: "2026-11-16T10:00:00Z", value: 1, status: "healthy", critical_threshold: 5, warning_threshold: 2 },
    { timestamp: "2026-11-16T11:00:00Z", value: 6, status: "critical", critical_threshold: 5, warning_threshold: 2 },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
  resetAlertStoreState();
  resetAlertSheetStoreState();
});

describe("ThresholdPreview", () => {
  it("sends live form edits and hydrated filters without waiting for the graph debounce", async () => {
    resetAlertStoreState();
    resetAlertSheetStoreState();
    useAlertStore.setState({ openSheetView: "monitor-1", selectedProject: definition.project });
    useAlertSheetStore.setState({ alertRuleDetails: normalizeAlertDetail({ ...definition, id: "monitor-1", notification_emails: [] }) });
    vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { result } });
    renderWithRouter(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <SnackbarProvider>
          <AlertSettingsForm onThresholdTypeChange={vi.fn()} setThresholdOperator={vi.fn()} setWarningValue={vi.fn()} setCriticalValue={vi.fn()} setFormIsDirty={vi.fn()} onPayloadChange={vi.fn()} />
        </SnackbarProvider>
      </QueryClientProvider>,
    );
    fireEvent.change(document.querySelector('[data-alert-field="critical-threshold-value"]'), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls.at(-1)[1]).toMatchObject({
      project: definition.project, metric_type: definition.metric_type,
      critical_threshold_value: 12, alert_frequency: 60,
      filters: definition.filters,
    });
  });
  it("requests the current unsaved definition only on demand and renders count and threshold series", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { result } });
    renderWithRouter(<ThresholdPreview definition={definition} />);
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("Would have fired in 1 of 2 intervals");
    expect(post).toHaveBeenCalledWith(`${endpoints.project.createMonitor}preview/`, definition, expect.any(Object));
    expect(screen.getByTestId("threshold-series").textContent).toContain("Critical threshold");
    expect(screen.getByTestId("threshold-series").textContent).toContain("6");
  });

  it("uses the saved monitor route while previewing unsaved edits", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { result } });
    renderWithRouter(<ThresholdPreview definition={definition} monitorId="monitor-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(`${endpoints.project.createMonitor}monitor-1/preview/`, definition, expect.any(Object)));
  });

  it("hides stale results when a threshold or filter changes", async () => {
    vi.spyOn(axios, "post").mockResolvedValue({ data: { result } });
    const { rerender } = renderWithRouter(<ThresholdPreview definition={definition} />);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("Would have fired in 1 of 2 intervals");
    rerender(<ThresholdPreview definition={{ ...definition, critical_threshold_value: 10 }} />);
    expect(screen.queryByText("Would have fired in 1 of 2 intervals")).not.toBeInTheDocument();
  });

  it("allows preview after an in-flight definition is changed and restored without cancelling its replacement", async () => {
    let resolveCancelled;
    let resolveReplacement;
    const post = vi.spyOn(axios, "post")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveCancelled = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveReplacement = resolve; }));
    const { rerender } = renderWithRouter(<ThresholdPreview definition={definition} />);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByRole("button", { name: "Previewing…" })).toBeDisabled();
    const cancelledSignal = post.mock.calls[0][2].signal;

    rerender(<ThresholdPreview definition={{ ...definition, critical_threshold_value: 10 }} />);
    expect(cancelledSignal.aborted).toBe(true);
    rerender(<ThresholdPreview definition={definition} />);
    expect(screen.queryByRole("button", { name: "Previewing…" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(post).toHaveBeenCalledTimes(2);
    await act(async () => { resolveCancelled({ data: { result } }); });
    expect(screen.queryByText("Would have fired in 1 of 2 intervals")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previewing…" })).toBeDisabled();
    expect(post.mock.calls[1][2].signal.aborted).toBe(false);

    await act(async () => { resolveReplacement({ data: { result } }); });
    expect(screen.getByText("Would have fired in 1 of 2 intervals")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeEnabled();
  });

  it("shows an empty state rather than implying healthy traffic", async () => {
    vi.spyOn(axios, "post").mockResolvedValue({ data: { result: { ...result, series: [], bucket_count: 0, fire_count: 0 } } });
    renderWithRouter(<ThresholdPreview definition={definition} />);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("No complete intervals with data in this window.");
  });

  it("shows validation errors and allows retry", async () => {
    vi.spyOn(axios, "post").mockRejectedValue({ response: { data: { result: { alert_frequency: ["Ensure this value is greater than or equal to 5."] } } } });
    renderWithRouter(<ThresholdPreview definition={definition} />);
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Preview" })).not.toBeDisabled();
  });
});
