import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { palette } from "src/theme/palette";
import EvalPickerDrawer from "../EvalPickerDrawer";

// Code evals never need a model, so a skipConfig pass adds them directly.
// The LLM eval's detail (mocked below) has no model, so it still needs the
// config screen.
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
  {
    id: "eval-3",
    name: "Third eval",
    template_type: "single",
    eval_type: "llm",
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

vi.mock("src/sections/evals/hooks/useEvalDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  evalDetailQuery: (templateId) => ({
    queryKey: ["evals", "detail", templateId],
    queryFn: async () => ({ id: templateId, model: null }),
  }),
}));

// The real config screen is a 2000-line data-bound form; stub it with the
// eval it is on, the progress slot, and save/back buttons.
vi.mock("../EvalPickerConfigFull", () => ({
  default: (props) => (
    <div data-testid="config-stub">
      <div data-testid="config-eval">{props.evalData?.name}</div>
      <div data-testid="progress-slot">{props.progress}</div>
      <button
        type="button"
        onClick={() =>
          props.onSave({
            templateId: props.evalData?.id,
            name: props.evalData?.name,
            mapping: {},
          })
        }
      >
        stub-save
      </button>
      <button type="button" onClick={props.onBack}>
        stub-back
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

const tick = (name) =>
  fireEvent.click(screen.getByRole("checkbox", { name: `Select ${name}` }));

const addedIds = (onEvalAdded) =>
  onEvalAdded.mock.calls.map(([config]) => config.templateId || config.id);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("EvalPickerDrawer — built-in multi-select pass", () => {
  it("shows a live count and one Add action for the ticked rows, and nothing at 0", () => {
    renderDrawer({ multiSelect: true, onEvalAdded: vi.fn() });

    expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add selected" })
    ).not.toBeInTheDocument();

    tick("First eval");
    expect(screen.getByText("1 evaluation selected")).toBeInTheDocument();
    tick("Second eval");
    expect(screen.getByText("2 evaluations selected")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Select Second eval" })
    ).toBeChecked();

    tick("First eval");
    expect(screen.getByText("1 evaluation selected")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Select First eval" })
    ).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "Add selected" })
    ).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
  });

  it("walks each pick through its own config screen and closes after the last", async () => {
    const onEvalAdded = vi.fn();
    const onClose = vi.fn();
    renderDrawer({ multiSelect: true, onEvalAdded, onClose });

    tick("First eval");
    tick("Second eval");
    fireEvent.click(screen.getByRole("button", { name: "Add selected" }));

    await waitFor(() =>
      expect(screen.getByTestId("config-eval")).toHaveTextContent("First eval")
    );
    expect(screen.getByTestId("progress-slot")).toHaveTextContent(
      "Evaluation 1 of 2"
    );

    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));
    await waitFor(() =>
      expect(screen.getByTestId("config-eval")).toHaveTextContent("Second eval")
    );
    expect(screen.getByTestId("progress-slot")).toHaveTextContent(
      "Evaluation 2 of 2"
    );
    expect(onEvalAdded).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(addedIds(onEvalAdded)).toEqual(["eval-1", "eval-2"]);
  });

  it("with skipConfig, adds every pick that needs no config in one pass", async () => {
    const onEvalAdded = vi.fn();
    const onClose = vi.fn();
    renderDrawer({ multiSelect: true, skipConfig: true, onEvalAdded, onClose });

    tick("First eval");
    tick("Second eval");
    fireEvent.click(screen.getByRole("button", { name: "Add selected" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(addedIds(onEvalAdded)).toEqual(["eval-1", "eval-2"]);
    expect(screen.queryByTestId("config-stub")).not.toBeInTheDocument();
  });

  it("with skipConfig, a pick that still needs a model gets the config screen after the others are added", async () => {
    const onEvalAdded = vi.fn();
    const onClose = vi.fn();
    renderDrawer({ multiSelect: true, skipConfig: true, onEvalAdded, onClose });

    tick("First eval");
    tick("Third eval");
    fireEvent.click(screen.getByRole("button", { name: "Add selected" }));

    await waitFor(() =>
      expect(screen.getByTestId("config-eval")).toHaveTextContent("Third eval")
    );
    expect(addedIds(onEvalAdded)).toEqual(["eval-1"]);
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "stub-save" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(addedIds(onEvalAdded)).toEqual(["eval-1", "eval-3"]);
  });

  it("Back from a pass returns to the list with the picks not yet added still ticked", async () => {
    const onEvalAdded = vi.fn();
    renderDrawer({ multiSelect: true, onEvalAdded });

    tick("First eval");
    tick("Second eval");
    fireEvent.click(screen.getByRole("button", { name: "Add selected" }));
    await waitFor(() =>
      expect(screen.getByTestId("config-eval")).toHaveTextContent("First eval")
    );

    fireEvent.click(screen.getByRole("button", { name: "stub-back" }));

    expect(screen.getByText("2 evaluations selected")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Select First eval" })
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Select Second eval" })
    ).toBeChecked();
    expect(onEvalAdded).not.toHaveBeenCalled();
  });

  it("an eval the host already has can't be ticked", () => {
    renderDrawer({
      multiSelect: true,
      existingEvals: [{ id: "eval-1" }],
      onEvalAdded: vi.fn(),
    });

    const added = screen.getByRole("checkbox", { name: "Select First eval" });
    expect(added).toBeDisabled();
    expect(added).toBeChecked();
    expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
  });

  it("a host that passes its own onToggleSelect keeps the selection", () => {
    const onToggleSelect = vi.fn();
    renderDrawer({
      multiSelect: true,
      selectedIds: new Set(),
      onToggleSelect,
      onEvalAdded: vi.fn(),
    });

    tick("First eval");
    expect(onToggleSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: "eval-1" })
    );
    expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
  });
});
