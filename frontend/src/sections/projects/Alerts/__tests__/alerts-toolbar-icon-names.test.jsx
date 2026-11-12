import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import Actions from "../components/AlertsListView/Actions";

// W10-TRACING-OTHER-U06: the Alerts list toolbar's Filter and Columns
// controls are icon-only buttons. Each carries a name a screen reader
// announces and the same words as a tooltip on hover, with or without
// filters applied (the applied state wraps the icon in a dot badge).

const h = vi.hoisted(() => ({
  hasValidFilters: false,
  noop: () => {},
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
// The icons render nothing here, so a button's name can only come from
// its own label.
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
    hasValidFilters: h.hasValidFilters,
    setActiveFilters: h.noop,
    setProjectOptions: h.noop,
  }),
  useAlertFilterFields: () => [],
}));

beforeEach(() => {
  h.hasValidFilters = false;
});

describe("Alerts list toolbar icon buttons", () => {
  it.each([
    ["Filter alerts", false],
    ["Filter alerts", true],
    ["Show or hide columns", false],
  ])(
    "names the %s button and shows it as a tooltip (filters applied: %s)",
    async (name, applied) => {
      h.hasValidFilters = applied;
      const user = userEvent.setup();
      render(<Actions />);

      const button = screen.getByRole("button", { name });
      await user.hover(button);
      expect(await screen.findByRole("tooltip")).toHaveTextContent(name);
    },
  );
});
