import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { palette } from "src/theme/palette";
import AddEvalsDrawer from "../AddEvalsDrawer";

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

// Stand in for the real config screen: expose the primary label the wrapper
// drives and a save button that returns the eval it is currently mapping.
vi.mock("src/sections/common/EvalPicker/EvalPickerConfigFull", () => ({
  default: (props) => {
    const id = props.evalData?.templateId || props.evalData?.id;
    return (
      <div data-testid="config-stub">
        <div data-testid="primary-label">{props.primaryLabel || ""}</div>
        <div data-testid="progress-slot">{props.progress}</div>
        <button
          type="button"
          onClick={() =>
            props.onSave({ templateId: id, name: id, mapping: {} })
          }
        >
          stub-save
        </button>
      </div>
    );
  },
}));

const ENV = { id: "env-1", name: "Support", surface: "voice", evalPreset: [] };
const ENV_STATE = { scenarios: [{ id: "s1" }], agent: { typeId: "voice" } };

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
          <AddEvalsDrawer
            open
            onClose={vi.fn()}
            env={ENV}
            envState={ENV_STATE}
            existingIds={new Set()}
            {...props}
          />
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AddEvalsDrawer — multi-select batch", () => {
  it("renders a checkbox per row on the list step", () => {
    renderDrawer({ onAdd: vi.fn() });
    expect(screen.getAllByRole("checkbox")).toHaveLength(ITEMS.length);
    expect(screen.getByText("First eval")).toBeInTheDocument();
  });

  it("ticks several, walks each config with the progress bar, then adds all at once", async () => {
    const onAdd = vi.fn();
    const onClose = vi.fn();
    renderDrawer({ onAdd, onClose });

    // Tick both evals → "Add Evaluations (2)".
    const boxes = screen.getAllByRole("checkbox");
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);
    fireEvent.click(
      screen.getByRole("button", { name: "Add Evaluations (2)" })
    );

    // Now walking the queue: first eval's config, primary reads "Next".
    expect(screen.getByTestId("config-stub")).toBeInTheDocument();
    expect(screen.getByTestId("primary-label")).toHaveTextContent("Next");
    expect(screen.getByText("Completion rate")).toBeInTheDocument();

    // Save eval #1 → advance to eval #2, primary now reads the final label.
    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));
    await waitFor(() =>
      expect(screen.getByTestId("primary-label")).toHaveTextContent(
        "Add 2 evaluations"
      )
    );
    expect(onAdd).not.toHaveBeenCalled();

    // Save eval #2 → the whole batch is handed over at once; the wrapper calls
    // onClose (EvalsStep owns `open`) and resets back to the list step.
    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    expect(onAdd).toHaveBeenCalledWith([
      expect.objectContaining({ id: "eval-1", custom: true }),
      expect.objectContaining({ id: "eval-2", custom: true }),
    ]);
    expect(onClose).toHaveBeenCalled();
    // Queue drained → back to the multi-select list, no config screen.
    await waitFor(() =>
      expect(screen.queryByTestId("config-stub")).not.toBeInTheDocument()
    );
  });

  it("a single row Add maps just that eval and keeps the drawer open", async () => {
    const onAdd = vi.fn();
    const onClose = vi.fn();
    renderDrawer({ onAdd, onClose });

    // Row "Add" (not the checkbox) → config → save.
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));

    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    expect(onAdd).toHaveBeenCalledWith([
      expect.objectContaining({ id: "eval-1", custom: true }),
    ]);
    // Single add is not a queue — the drawer stays open for more.
    expect(onClose).not.toHaveBeenCalled();
  });
});
