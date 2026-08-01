import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";

import AutocompleteTextValueSelector from "../AutocompleteTextValueSelector";

vi.mock("react-router-dom", () => ({
  useParams: () => ({ id: "project-1" }),
}));

vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    project: {
      spanAttributeValues: () => "/api/traces/span-attribute-values/",
    },
  },
}));

const renderSelector = (updateFilter = vi.fn()) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return {
    updateFilter,
    ...render(
      <QueryClientProvider client={queryClient}>
        <AutocompleteTextValueSelector
          definition={{ propertyId: "final_status" }}
          filter={{ filter_config: { filter_value: "" } }}
          updateFilter={updateFilter}
        />
      </QueryClientProvider>,
    ),
  };
};

describe("AutocompleteTextValueSelector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps partial suggestions available while warning they are incomplete", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: [{ value: "completed", count: 4 }],
        query_complete: false,
        query_status: "degraded",
      },
    });
    renderSelector();

    await waitFor(() =>
      expect(
        screen.getByText(
          "Value suggestions are incomplete. Type a value to continue.",
        ),
      ).toBeInTheDocument(),
    );

    const input = screen.getByRole("combobox");
    await userEvent.click(input);
    await userEvent.keyboard("{ArrowDown}");
    expect(
      await screen.findByRole("option", { name: "completed" }),
    ).toBeVisible();
  });

  it("warns once and still accepts a manually typed value when suggestions degrade", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: [],
        query_complete: false,
        query_status: "degraded",
        query_error_code: "read_budget_exceeded",
      },
    });
    const updateFilter = vi.fn();
    renderSelector(updateFilter);

    const warning =
      "Value suggestions are incomplete. Type a value to continue.";
    await waitFor(() => expect(screen.getByText(warning)).toBeInTheDocument());
    expect(screen.getAllByText(warning)).toHaveLength(1);

    const input = screen.getByRole("combobox");
    await userEvent.type(input, "manual-status");
    fireEvent.blur(input);

    await waitFor(() =>
      expect(updateFilter).toHaveBeenCalledWith({
        filter_config: { filter_value: "manual-status" },
      }),
    );
  });
});
