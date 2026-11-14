import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  sheet: {
    handleViewTrace: vi.fn(),
    alertRuleDetails: null,
    refreshGrid: vi.fn(),
    setAlertRuleDetails: vi.fn(),
  },
  handleCloseSheetView: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(() => new Promise(() => {})) },
  endpoints: { project: { getAlertGraph: (id) => `/alerts/${id}/graph/` } },
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/sections/projects/LLMTracing/TracingControls", () => ({
  default: () => null,
}));
vi.mock("../components/AlertsChart", () => ({ default: () => null }));
vi.mock("../components/DuplicateALert", () => ({ default: () => null }));
vi.mock("../components/AlertsSheetView/Issues", () => ({
  default: () => null,
}));
vi.mock("../components/AlertsSheetView/AlertDetails", () => ({
  default: () => null,
}));
vi.mock("../useMuteAlerMutation", () => ({
  useMuteAlertsMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../store/useAlertStore", () => ({
  useAlertStore: () => ({
    openCreateAlerts: false,
    openSheetView: "alert-1",
    handleCloseSheetView: mocks.handleCloseSheetView,
    handleProjectChange: vi.fn(),
    handleStartCreatingAlerts: vi.fn(),
    setCurrentTab: vi.fn(),
    setOpenCreateAlerts: vi.fn(),
    mainPage: false,
    refreshGrid: vi.fn(),
  }),
}));
vi.mock("../store/useAlertSheetView", () => ({
  useAlertSheetView: () => mocks.sheet,
}));

import AlertsSheetView from "../components/AlertsSheetView/AlertsSheetView";

const renderSheet = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AlertsSheetView />
    </QueryClientProvider>,
  );

const viewTraceButton = () =>
  document.querySelector('[data-alert-sheet-action="view-trace"]');

describe("AlertsSheetView header View Trace", () => {
  beforeEach(() => {
    mocks.sheet.handleViewTrace.mockReset();
    mocks.handleCloseSheetView.mockReset();
  });

  it("is disabled until the alert details have loaded", () => {
    mocks.sheet.alertRuleDetails = null;
    renderSheet();

    expect(viewTraceButton()).toBeDisabled();
    fireEvent.click(viewTraceButton());
    expect(mocks.sheet.handleViewTrace).not.toHaveBeenCalled();
    expect(mocks.handleCloseSheetView).not.toHaveBeenCalled();
  });

  it("opens the trace view once the details are there", () => {
    mocks.sheet.alertRuleDetails = {
      id: "alert-1",
      name: "Latency",
      project: "project-1",
    };
    renderSheet();

    expect(screen.getByText("Latency")).toBeInTheDocument();
    expect(viewTraceButton()).toBeEnabled();
    fireEvent.click(viewTraceButton());
    expect(mocks.sheet.handleViewTrace).toHaveBeenCalledTimes(1);
    expect(mocks.handleCloseSheetView).toHaveBeenCalledTimes(1);
  });
});
