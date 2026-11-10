import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import ObjectFilterPanel from "../ObjectFilterPanel";

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));

// The Evals list fields: a multi-select enum with display labels, and a
// single-select enum.
const filterFields = [
  {
    value: "output_type",
    label: "Output Type",
    type: "enum",
    choices: ["pass_fail", "percentage", "deterministic"],
    choiceLabels: {
      pass_fail: "Pass/fail",
      percentage: "Percentage",
      deterministic: "Choices",
    },
  },
  {
    value: "template_type",
    label: "Type",
    type: "enum",
    choices: ["single", "composite"],
    single: true,
  },
];

const renderPanel = (onApply = vi.fn()) => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ObjectFilterPanel
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        filterFields={filterFields}
        currentFilters={null}
        onApply={onApply}
      />
    </QueryClientProvider>,
  );
  return onApply;
};

const pickProperty = async (name) => {
  fireEvent.click(await screen.findByRole("button", { name: /^property/i }));
  fireEvent.click(await screen.findByText(name));
};

describe("ObjectFilterPanel enum fields", () => {
  it("lists the options with their labels instead of a text box", async () => {
    renderPanel();
    await pickProperty("Output Type");

    expect(
      screen.queryByPlaceholderText("Enter text..."),
    ).not.toBeInTheDocument();
    fireEvent.click(await screen.findByText("Select values..."));

    const options = await screen.findAllByRole("checkbox");
    expect(
      options.map((o) => o.getAttribute("data-filter-value-option")),
    ).toEqual(["pass_fail", "percentage", "deterministic"]);
    expect(screen.getByText("Pass/fail")).toBeInTheDocument();
    expect(screen.getByText("Percentage")).toBeInTheDocument();
    expect(screen.getByText("Choices")).toBeInTheDocument();
  });

  it("applies the picked values as the object payload", async () => {
    const onApply = renderPanel();
    await pickProperty("Output Type");
    fireEvent.click(await screen.findByText("Select values..."));
    fireEvent.click(await screen.findByText("Percentage"));
    fireEvent.click(screen.getByText("Choices"));

    await waitFor(() =>
      expect(onApply).toHaveBeenLastCalledWith({
        output_type: ["percentage", "deterministic"],
      }),
    );
  });

  it("offers a single-select list for a single-value field", async () => {
    const onApply = renderPanel();
    await pickProperty("Type");
    fireEvent.click(await screen.findByText("Select a value..."));

    const options = await screen.findAllByRole("radio");
    expect(options).toHaveLength(2);
    fireEvent.click(screen.getByText("composite"));

    await waitFor(() =>
      expect(onApply).toHaveBeenLastCalledWith({
        template_type: ["composite"],
      }),
    );
  });
});
