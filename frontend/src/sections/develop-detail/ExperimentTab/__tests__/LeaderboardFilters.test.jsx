import React from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import LeaderboardFilters from "../LeaderboardFilters";

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));

const trialsFilter = {
  column_id: "trials",
  filter_config: {
    filter_type: "number",
    filter_op: "equals",
    filter_value: ["3", ""],
  },
};

const mountFilters = (overrides = {}) => {
  const props = {
    open: true,
    onClose: vi.fn(),
    filters: [],
    onChange: vi.fn(),
    runs: [],
    metricDefs: [],
    ...overrides,
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (next) => (
    <QueryClientProvider client={client}>
      <LeaderboardFilters {...next} />
    </QueryClientProvider>
  );
  const rendered = render(view(props));
  return {
    ...rendered,
    props,
    rerenderFilters: (next) => rendered.rerender(view({ ...props, ...next })),
  };
};

describe("LeaderboardFilters", () => {
  it("adds a filter and applies only a completed value to the parent", async () => {
    const { props } = mountFilters();
    fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
    fireEvent.click(await screen.findByRole("button", { name: "Property" }));
    fireEvent.click(
      document.querySelector('[data-filter-property-option="trials"]'),
    );
    expect(props.onChange).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText("Value"), {
      target: { value: "3" },
    });
    await waitFor(() =>
      expect(props.onChange).toHaveBeenLastCalledWith([
        expect.objectContaining(trialsFilter),
      ]),
    );
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("resets the editor when reopened after external filters are cleared", async () => {
    const { rerenderFilters, props } = mountFilters({
      filters: [trialsFilter],
    });
    fireEvent.click(screen.getByRole("button", { name: /Add filter/ }));
    const valueInput = await screen.findByPlaceholderText("Value");
    expect(valueInput).toHaveValue("3");
    fireEvent.keyDown(valueInput, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByPlaceholderText("Value")).not.toBeInTheDocument(),
    );

    rerenderFilters({ filters: [] });
    fireEvent.click(screen.getByRole("button", { name: /Add filter/ }));
    expect(
      await screen.findByRole("button", { name: "Property" }),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Value")).not.toBeInTheDocument();
    expect(props.onChange).not.toHaveBeenCalled();
  });

  it("keeps the dataset version and clear/done callbacks", () => {
    const onVersion = vi.fn();
    const { props } = mountFilters({
      filters: [trialsFilter],
      version: 2,
      versions: [{ number: 2 }],
      onVersion,
    });
    expect(screen.getByTestId("filter-version")).toHaveTextContent(
      "Only runs on this version",
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(props.onChange).toHaveBeenLastCalledWith([]);
    expect(onVersion).toHaveBeenLastCalledWith("all");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(props.onClose).toHaveBeenCalledOnce();
  });
});
