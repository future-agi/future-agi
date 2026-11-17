import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { render } from "src/utils/test-utils";

// The Edit Rule / Duplicate drawer names its preview chart (legend and y-axis
// unit) from the alert store's alertType. Opening that drawer from an existing
// alert has to put the alert's own metric type there first, or a Token usage
// alert previews as "Span Response Time" in ms.

const mocks = vi.hoisted(() => ({
  sheet: {
    handleViewTrace: vi.fn(),
    alertRuleDetails: null,
    refreshGrid: vi.fn(),
    setAlertRuleDetails: vi.fn(),
  },
  handleChangeAlertType: vi.fn(),
  handleStartCreatingAlerts: vi.fn(),
  setCurrentTab: vi.fn(),
  setOpenCreateAlerts: vi.fn(),
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
vi.mock("../components/DuplicateALert", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ open, onAction }) =>
    open ? (
      <button
        type="button"
        data-testid="duplicate-alert-confirm"
        onClick={onAction}
      >
        Confirm duplicate
      </button>
    ) : null,
}));
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
    handleCloseSheetView: vi.fn(),
    handleProjectChange: vi.fn(),
    handleStartCreatingAlerts: mocks.handleStartCreatingAlerts,
    handleChangeAlertType: mocks.handleChangeAlertType,
    setCurrentTab: mocks.setCurrentTab,
    setOpenCreateAlerts: mocks.setOpenCreateAlerts,
    mainPage: false,
    refreshGrid: vi.fn(),
  }),
}));
vi.mock("../store/useAlertSheetView", () => ({
  useAlertSheetView: () => mocks.sheet,
}));

import AlertsSheetView from "../components/AlertsSheetView/AlertsSheetView";

const tokenUsageAlert = {
  id: "alert-1",
  name: "Token budget",
  project: "project-1",
  metricType: "token_usage",
};

const renderSheet = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AlertsSheetView />
    </QueryClientProvider>,
  );

const sheetAction = (action) =>
  document.querySelector(`[data-alert-sheet-action="${action}"]`);

const firstCallOrder = (fn) => fn.mock.invocationCallOrder[0];

describe("AlertsSheetView opens the alert drawer on the alert's own metric", () => {
  beforeEach(() => {
    mocks.handleChangeAlertType.mockReset();
    mocks.handleStartCreatingAlerts.mockReset();
    mocks.setCurrentTab.mockReset();
    mocks.setOpenCreateAlerts.mockReset();
    mocks.sheet.alertRuleDetails = null;
  });

  it("Edit Rule sets the alert type to the saved metric before opening the drawer", () => {
    mocks.sheet.alertRuleDetails = tokenUsageAlert;
    renderSheet();

    fireEvent.click(sheetAction("edit"));

    expect(mocks.handleChangeAlertType).toHaveBeenCalledTimes(1);
    expect(mocks.handleChangeAlertType).toHaveBeenCalledWith("token_usage");
    expect(mocks.setOpenCreateAlerts).toHaveBeenCalledWith(true);
    expect(firstCallOrder(mocks.handleChangeAlertType)).toBeLessThan(
      firstCallOrder(mocks.setOpenCreateAlerts),
    );
  });

  it("Duplicate sets the alert type to the saved metric before opening the drawer", () => {
    mocks.sheet.alertRuleDetails = tokenUsageAlert;
    renderSheet();

    fireEvent.click(sheetAction("duplicate"));
    fireEvent.click(screen.getByTestId("duplicate-alert-confirm"));

    expect(mocks.handleChangeAlertType).toHaveBeenCalledTimes(1);
    expect(mocks.handleChangeAlertType).toHaveBeenCalledWith("token_usage");
    expect(mocks.handleStartCreatingAlerts).toHaveBeenCalledTimes(1);
    expect(firstCallOrder(mocks.handleChangeAlertType)).toBeLessThan(
      firstCallOrder(mocks.handleStartCreatingAlerts),
    );
  });

  it("Duplicate before the details load leaves the alert type alone", () => {
    renderSheet();

    fireEvent.click(sheetAction("duplicate"));
    fireEvent.click(screen.getByTestId("duplicate-alert-confirm"));

    expect(mocks.handleStartCreatingAlerts).toHaveBeenCalledTimes(1);
    expect(mocks.handleChangeAlertType).not.toHaveBeenCalled();
  });
});
