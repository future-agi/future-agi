import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { palette } from "src/theme/palette";
import EvalPickerDrawer from "../EvalPickerDrawer";

// The list's data comes from a paged react-query hook; feed it a fixed page.
const ITEMS = [
  {
    id: "eval-1",
    name: "First eval",
    template_type: "single",
    eval_type: "llm",
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

vi.mock("../hooks/useEvalPickerData", () => ({
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

// The real config screen is a 2000-line data-bound form; stub it with a marker
// that forwards the caller-supplied decorations and a save button.
vi.mock("../EvalPickerConfigFull", () => ({
  default: (props) => (
    <div data-testid="config-stub">
      <div data-testid="primary-label">{props.primaryLabel || ""}</div>
      <div data-testid="progress-slot">{props.progress}</div>
      {props.showClose ? <span data-testid="config-close" /> : null}
      <button
        type="button"
        onClick={() =>
          props.onSave({ templateId: "eval-1", name: "First eval", mapping: {} })
        }
      >
        stub-save
      </button>
    </div>
  ),
}));

const theme = createTheme({
  palette: palette("light"),
  spacing: (f) => `${0.25 * f}rem`,
});

function renderDrawer(props) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <ThemeProvider theme={theme}>
          <EvalPickerDrawer open onClose={vi.fn()} source="dataset" {...props} />
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("EvalPickerDrawer — single-add path is unchanged without multiSelect", () => {
  it("renders no checkboxes and no headerAction", () => {
    renderDrawer({ onEvalAdded: vi.fn() });

    expect(screen.getByText("First eval")).toBeInTheDocument();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(
      screen.queryByRole("button", { name: /Add Evaluations/ })
    ).not.toBeInTheDocument();
  });

  it("fires onEvalAdded once per config save", () => {
    const onEvalAdded = vi.fn();
    renderDrawer({ onEvalAdded });

    // Row "Add" → config step → save.
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
    expect(screen.getByTestId("config-stub")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));

    expect(onEvalAdded).toHaveBeenCalledTimes(1);
    expect(onEvalAdded).toHaveBeenCalledWith(
      expect.objectContaining({ templateId: "eval-1" })
    );
  });
});

describe("EvalPickerDrawer — multiSelect batch props", () => {
  it("renders a checkbox per row, the headerAction, and toggles selection", () => {
    const onToggleSelect = vi.fn();
    renderDrawer({
      multiSelect: true,
      selectedIds: new Set(),
      onToggleSelect,
      headerAction: <button type="button">Add Evaluations (0)</button>,
      onEvalAdded: vi.fn(),
    });

    expect(screen.getAllByRole("checkbox")).toHaveLength(ITEMS.length);
    expect(
      screen.getByRole("button", { name: "Add Evaluations (0)" })
    ).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expect(onToggleSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: "eval-1" })
    );
  });

  it("passes progress and primaryLabel through to the config screen", () => {
    renderDrawer({
      initialEval: { id: "eval-1", name: "First eval" },
      primaryLabel: "Next",
      progress: <div>bar</div>,
      showClose: true,
      onEvalAdded: vi.fn(),
    });

    expect(screen.getByTestId("primary-label")).toHaveTextContent("Next");
    expect(screen.getByTestId("progress-slot")).toHaveTextContent("bar");
    expect(screen.getByTestId("config-close")).toBeInTheDocument();
  });
});

describe("EvalPickerDrawer — edit-mode close gate", () => {
  it("closes on edit save by default (existing edit callers unchanged)", async () => {
    const onClose = vi.fn();
    renderDrawer({
      initialEval: { id: "eval-1", name: "First eval" },
      onEvalAdded: vi.fn(),
      onClose,
    });

    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("stays open on edit save when keepOpenAfterEditSave is set", async () => {
    const onClose = vi.fn();
    const onEvalAdded = vi.fn();
    renderDrawer({
      initialEval: { id: "eval-1", name: "First eval" },
      keepOpenAfterEditSave: true,
      onEvalAdded,
      onClose,
    });

    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));
    await waitFor(() => expect(onEvalAdded).toHaveBeenCalledTimes(1));
    expect(onClose).not.toHaveBeenCalled();
  });
});
