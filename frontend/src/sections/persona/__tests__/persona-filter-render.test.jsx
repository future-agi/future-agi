import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

import FilterPanel from "src/components/filter-panel/FilterPanel";
import { AGENT_TYPES } from "src/sections/agents/constants";

// Mirrors PersonaListView's `filterFields` (PersonaListView.jsx:441-468).
// Persona has no filter rendering test of its own; this pins the behaviour
// that changed when `choiceLabels` was wired into the enum picker.
const PERSONA_FILTER_FIELDS = [
  {
    value: "type",
    label: "Category",
    type: "enum",
    choices: ["prebuilt", "custom"],
    operators: ["is"],
    single: true,
  },
  {
    value: "simulation_type",
    label: "Agent Type",
    type: "enum",
    choices: [AGENT_TYPES.VOICE, AGENT_TYPES.CHAT],
    choiceLabels: {
      [AGENT_TYPES.VOICE]: "Voice",
      [AGENT_TYPES.CHAT]: "Chat",
    },
    operators: ["is"],
    single: true,
  },
];

const renderPanel = (currentFilters = null) =>
  render(
    <FilterPanel
      anchorEl={document.body}
      open
      onClose={vi.fn()}
      filterFields={PERSONA_FILTER_FIELDS}
      currentFilters={currentFilters}
      onApply={vi.fn()}
    />,
  );

const openValuePicker = (rowIndex = 0) => {
  fireEvent.click(screen.getAllByText("Select values...")[rowIndex]);
  return within(
    screen.getByPlaceholderText("Search values...").closest(".MuiPopover-paper"),
  );
};

describe("Persona filter panel", () => {
  it("shows human labels for the field that supplies choiceLabels", () => {
    renderPanel({ simulation_type: [] });
    // Row defaults to the first field, so switch to Agent Type first.
    fireEvent.mouseDown(screen.getAllByRole("combobox")[0]);
    fireEvent.click(within(screen.getByRole("listbox")).getByText("Agent Type"));

    const picker = openValuePicker();
    expect(picker.getByText("Voice")).toBeInTheDocument();
    expect(picker.getByText("Chat")).toBeInTheDocument();
    expect(picker.queryByText(AGENT_TYPES.VOICE)).not.toBeInTheDocument();
  });

  it("still shows raw values for the field with no choiceLabels", () => {
    renderPanel();
    const picker = openValuePicker();
    expect(picker.getByText("prebuilt")).toBeInTheDocument();
    expect(picker.getByText("custom")).toBeInTheDocument();
  });

  it("stops a second row once both single-value fields are used", () => {
    renderPanel();
    const addFilter = screen.getByRole("button", { name: /add filter/i });

    expect(addFilter).toBeEnabled();
    fireEvent.click(addFilter);
    expect(screen.getByText("Agent Type")).toBeInTheDocument();
    expect(addFilter).toBeDisabled();
  });
});
