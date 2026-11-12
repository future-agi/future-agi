import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import EmptyAlerts from "../components/EmptyAlerts";
import Actions from "../components/AlertsListView/Actions";

// W10-TRACING-OTHER-U07: a Viewer may read alerts but not create them.
// Both places that create an alert (the first-run empty state and the
// list toolbar) keep the action disabled for a Viewer and say why (as text
// under the empty-state action, on hover in the toolbar); a role that may
// create alerts gets the action with no reason.

const REASON =
  "Viewers can't create alerts. Ask a workspace admin for Member access.";

const h = vi.hoisted(() => ({
  role: "Owner",
  noop: () => {},
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: h.role }),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({
  default: () => null,
}));
vi.mock("src/components/filter", () => ({ ObjectFilterPanel: () => null }));
vi.mock("../components/AlertsListView/RowActions", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/LLMTracing/common", () => ({
  useProjectList: () => ({ data: [] }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: { createNewAlertClicked: "create_new_alert_clicked" },
  PropertyName: { click: "click" },
  trackEvent: () => {},
  handleOnDocsClicked: () => {},
}));
vi.mock("../store/useAlertStore", () => ({
  useAlertStore: () => ({
    searchQuery: "",
    onSearchQueryChange: h.noop,
    handleStartCreatingAlerts: h.noop,
    selectedRows: [],
    columns: [],
    setColumns: h.noop,
    selectedAll: false,
    setCurrentTab: h.noop,
    selectedProject: "project-1",
    handleOpenProjectModal: h.noop,
    mainPage: false,
  }),
}));
vi.mock("../store/useAlertFilterStore", () => ({
  useAlertFilterShallow: () => ({
    activeFilters: [],
    hasValidFilters: false,
    setActiveFilters: h.noop,
    setProjectOptions: h.noop,
  }),
  useAlertFilterFields: () => [],
}));

const SURFACES = [
  ["empty state", "Create alert"],
  ["list toolbar", "New Alert"],
];

const renderSurface = (surface) =>
  render(
    surface === "empty state" ? (
      <EmptyAlerts onStartCreatingAlerts={h.noop} mainPage={false} />
    ) : (
      <Actions />
    ),
  );

beforeEach(() => {
  h.role = "Owner";
});

describe("Creating an alert as a Viewer", () => {
  describe.each(SURFACES)("on the %s", (surface, label) => {
    it.each(["Viewer", "workspace_viewer"])(
      "keeps %s's action disabled and says why on hover",
      async (role) => {
        h.role = role;
        const user = userEvent.setup();
        renderSurface(surface);

        const action = screen.getByRole("button", { name: label });
        expect(action).toBeDisabled();
        if (surface === "empty state") {
          // The empty state shows the reason as text under the action.
          expect(screen.getByText(REASON)).toBeVisible();
          return;
        }
        // A disabled button fires no pointer events: the reason sits on
        // its wrapper.
        await user.hover(action.parentElement);
        expect(await screen.findByRole("tooltip")).toHaveTextContent(REASON);
      },
    );

    it("offers a Member the action with no reason", async () => {
      h.role = "Member";
      const user = userEvent.setup();
      renderSurface(surface);

      const action = screen.getByRole("button", { name: label });
      expect(action).toBeEnabled();
      await user.hover(action.parentElement);
      await expect(
        screen.findByRole("tooltip", {}, { timeout: 300 }),
      ).rejects.toThrow();
      expect(screen.queryByText(REASON)).toBeNull();
    });
  });
});
