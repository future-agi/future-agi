/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { palette } from "src/theme/palette";
import { useEvalsList } from "src/sections/evals/hooks/useEvalsList";
import EvalPickerDrawer from "./EvalPickerDrawer";

// W10-FILTERS-U02: the picker opened from Tasks (source="task") reads the
// templates list endpoint. The Filter panel's "Type" and "Name" rows must
// change the list, and the Filter button must count them.

const catalog = vi.hoisted(() => ({
  rows: [
    {
      id: "tpl-toxicity",
      name: "Toxicity check",
      template_type: "single",
      eval_type: "llm",
      output_type: "pass_fail",
      created_by_name: "System",
    },
    {
      id: "tpl-relevance",
      name: "Answer relevance",
      template_type: "single",
      eval_type: "llm",
      output_type: "percentage",
      created_by_name: "System",
    },
    {
      id: "tpl-composite",
      name: "Composite safety",
      template_type: "composite",
      eval_type: "llm",
      output_type: "pass_fail",
      created_by_name: "Maya Chen",
    },
  ],
}));

vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));

// Stands in for POST eval-templates/list. It keeps the request contract of
// EvalListRequestSerializer: `filters.template_type` is a list (a bare
// string is a 400, so the page gets no rows) and `search` matches names.
vi.mock("src/sections/evals/hooks/useEvalsList", async (importOriginal) => {
  const actual = await importOriginal();
  const useEvalsListStub = vi.fn(({ search, filters }) => {
    const templateTypes = filters?.template_type;
    if (templateTypes !== undefined && !Array.isArray(templateTypes)) {
      return {
        data: undefined,
        isError: true,
        isLoading: false,
        isFetching: false,
      };
    }
    const term = (search || "").toLowerCase();
    const rows = catalog.rows.filter((row) => {
      if (templateTypes?.length && !templateTypes.includes(row.template_type))
        return false;
      if (term && !row.name.toLowerCase().includes(term)) return false;
      return true;
    });
    return {
      data: { items: rows, total: rows.length },
      isError: false,
      isLoading: false,
      isFetching: false,
    };
  });
  return { ...actual, useEvalsList: useEvalsListStub };
});

// The panel hands its rows to the real adapter, as EvalFilterPanel does.
vi.mock("src/sections/evals/components/EvalFilterPanel", async () => {
  const { rowsToFilters } = await import(
    "src/sections/evals/components/evalFilterAdapter"
  );
  const FilterPanelStub = ({ open, onApply, onClose }) =>
    open ? (
      <div>
        <button
          type="button"
          onClick={() =>
            onApply(
              rowsToFilters([
                {
                  field: "template_type",
                  fieldType: "categorical",
                  operator: "equals",
                  value: ["composite"],
                },
              ]),
            )
          }
        >
          Type is composite
        </button>
        <button
          type="button"
          onClick={() =>
            onApply(
              rowsToFilters([
                {
                  field: "name",
                  fieldType: "string",
                  operator: "contains",
                  value: ["toxicity"],
                },
              ]),
            )
          }
        >
          Name contains toxicity
        </button>
        <button type="button" onClick={onClose}>
          Close filters
        </button>
      </div>
    ) : null;
  return { default: FilterPanelStub };
});

vi.mock("./EvalPickerConfigFull", () => {
  const ConfigStub = () => null;
  return { default: ConfigStub };
});

const theme = createTheme({
  palette: palette("light"),
  spacing: (f) => `${0.25 * f}rem`,
});

// Same props TaskConfigPanel passes: source "task" with the project id.
const renderTaskPicker = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <BrowserRouter>
        <ThemeProvider theme={theme}>
          <EvalPickerDrawer
            open
            onClose={vi.fn()}
            source="task"
            sourceId="project-1"
            onEvalAdded={vi.fn()}
          />
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>,
  );

const applyFilter = (label) => {
  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  fireEvent.click(screen.getByRole("button", { name: label }));
  fireEvent.click(screen.getByRole("button", { name: "Close filters" }));
};

beforeEach(() => {
  vi.mocked(useEvalsList).mockClear();
});

describe("W10-FILTERS-U02 · eval picker filters from Tasks", () => {
  it("shows only composite evals after Type is composite and counts the filter", () => {
    renderTaskPicker();
    expect(screen.getByText("Toxicity check")).toBeInTheDocument();
    expect(screen.getByText("Answer relevance")).toBeInTheDocument();
    expect(screen.getByText("Composite safety")).toBeInTheDocument();

    applyFilter("Type is composite");

    expect(screen.getByText("Composite safety")).toBeInTheDocument();
    expect(screen.queryByText("Toxicity check")).toBeNull();
    expect(screen.queryByText("Answer relevance")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Filter (1)" }),
    ).toBeInTheDocument();
    expect(useEvalsList).toHaveBeenLastCalledWith(
      expect.objectContaining({ filters: { template_type: ["composite"] } }),
    );
  });

  it("shows only matching names after Name contains and counts the filter", () => {
    renderTaskPicker();

    applyFilter("Name contains toxicity");

    expect(screen.getByText("Toxicity check")).toBeInTheDocument();
    expect(screen.queryByText("Answer relevance")).toBeNull();
    expect(screen.queryByText("Composite safety")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Filter (1)" }),
    ).toBeInTheDocument();
    expect(useEvalsList).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: "toxicity" }),
    );
  });
});
