import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import { ObjectFilterPanel } from "src/components/filter";
import { AGENT_TYPES } from "src/sections/agents/constants";

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));

// Mirrors PersonaListView's `filterFields`. Pins that choiceLabels reach the
// picker (users see "Voice", never the stored value) and that the panel hands
// back the same object PersonaListView's apply handler reads.
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

const renderPanel = (currentFilters = null, onApply = vi.fn()) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ObjectFilterPanel
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        filterFields={PERSONA_FILTER_FIELDS}
        currentFilters={currentFilters}
        onApply={onApply}
        aiPlaceholder="e.g. 'show custom voice personas'"
      />
    </QueryClientProvider>,
  );

describe("Persona filter panel", () => {
  it("shows the stored value as its human label", async () => {
    renderPanel({ simulation_type: [AGENT_TYPES.VOICE] });
    expect(await screen.findByText("Agent Type")).toBeInTheDocument();
    expect(screen.getByText("Voice")).toBeInTheDocument();
    expect(screen.queryByText(AGENT_TYPES.VOICE)).not.toBeInTheDocument();
  });

  it("shows raw values for the field with no choiceLabels", async () => {
    renderPanel({ type: ["custom"] });
    expect(await screen.findByText("Category")).toBeInTheDocument();
    expect(screen.getByText("custom")).toBeInTheDocument();
  });

  it("uses the page's own Ask AI example", () => {
    renderPanel();
    expect(
      screen.getByPlaceholderText("e.g. 'show custom voice personas'"),
    ).toBeInTheDocument();
  });

  it("clears with null, as PersonaListView expects", async () => {
    const onApply = vi.fn();
    renderPanel({ type: ["custom"] }, onApply);
    fireEvent.click(await screen.findByRole("button", { name: /clear all/i }));
    expect(onApply).toHaveBeenCalledWith(null);
  });
});
