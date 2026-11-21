import React, { useState } from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { palette } from "src/theme/palette";
import CompositeDetailPanel from "../CompositeDetailPanel";

// Code evals never need a model, so the composite's child picker adds them
// straight from the list.
const ITEMS = [
  {
    id: "eval-1",
    name: "First eval",
    template_type: "single",
    eval_type: "code",
    output_type: "pass_fail",
    created_by_name: "System",
  },
  {
    id: "eval-2",
    name: "Second eval",
    template_type: "single",
    eval_type: "code",
    output_type: "pass_fail",
    created_by_name: "System",
  },
];

vi.mock("src/sections/common/EvalPicker/hooks/useEvalPickerData", () => ({
  useEvalPickerData: () => ({
    items: ITEMS,
    total: ITEMS.length,
    isLoading: false,
    isSearching: false,
    searchQuery: "",
    setSearchQuery: vi.fn(),
    page: 0,
    setPage: vi.fn(),
    pageSize: 10,
    setPageSize: vi.fn(),
    sorting: [],
    setSorting: vi.fn(),
    filters: null,
    setFilters: vi.fn(),
  }),
}));

vi.mock("src/sections/common/EvalPicker/EvalPickerConfigFull", () => ({
  default: () => <div data-testid="config-stub" />,
}));

vi.mock("../../hooks/useCompositeChildrenKeys", async (importOriginal) => ({
  ...(await importOriginal()),
  useCompositeChildrenSchemas: () => ({}),
}));

const theme = createTheme({
  palette: palette("light"),
  spacing: (f) => `${0.25 * f}rem`,
});

// The panel is controlled; hold its children like the create/detail pages do.
function Harness({ onChildrenChange }) {
  const [childrenList, setChildrenList] = useState([]);
  return (
    <CompositeDetailPanel
      editable
      name="my-composite"
      compositeChildAxis="pass_fail"
      children={childrenList}
      onChildrenChange={(next) => {
        setChildrenList(next);
        onChildrenChange(next);
      }}
    />
  );
}

Harness.propTypes = {
  onChildrenChange: PropTypes.func.isRequired,
};

function renderPanel(onChildrenChange) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <ThemeProvider theme={theme}>
          <Harness onChildrenChange={onChildrenChange} />
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

const lastChildren = (onChildrenChange) =>
  onChildrenChange.mock.calls[onChildrenChange.mock.calls.length - 1][0];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CompositeDetailPanel — child picker", () => {
  it("adds two children in one pass through the picker", async () => {
    const onChildrenChange = vi.fn();
    renderPanel(onChildrenChange);

    fireEvent.click(screen.getByRole("button", { name: "Add evaluation" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select First eval" })
    );
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select Second eval" })
    );
    fireEvent.click(screen.getByRole("button", { name: "Add selected" }));

    await waitFor(() =>
      expect(
        lastChildren(onChildrenChange).map((c) => [c.child_id, c.order])
      ).toEqual([
        ["eval-1", 0],
        ["eval-2", 1],
      ])
    );
    expect(screen.getByText("Children (2)")).toBeInTheDocument();
  });

  it("still adds a single child from a row's Add button", async () => {
    const onChildrenChange = vi.fn();
    renderPanel(onChildrenChange);

    fireEvent.click(screen.getByRole("button", { name: "Add evaluation" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);

    await waitFor(() => expect(onChildrenChange).toHaveBeenCalledTimes(1));
    expect(lastChildren(onChildrenChange).map((c) => c.child_id)).toEqual([
      "eval-1",
    ]);
  });
});
