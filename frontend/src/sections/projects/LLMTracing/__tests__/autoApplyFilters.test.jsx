import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TraceFilterPanel, {
  hasIncompleteNumericRow,
  serializeFilterSet,
} from "../TraceFilterPanel";

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: () => ({
    data: [],
    isLoading: false,
    isError: false,
  }),
}));

// ---------------------------------------------------------------------------
// hasIncompleteNumericRow — the gate that holds auto-apply while a numeric row
// is mid-edit (blocking review item 1: invalid numerics must not reach the API,
// and a half-filled range must not drop the applied filter).
// ---------------------------------------------------------------------------
describe("hasIncompleteNumericRow", () => {
  const numRow = (value) => ({ fieldType: "number", value });

  it("holds on a partial/invalid scalar value", () => {
    expect(hasIncompleteNumericRow([numRow("-")])).toBe(true);
    expect(hasIncompleteNumericRow([numRow(".")])).toBe(true);
    expect(hasIncompleteNumericRow([numRow("1.5.6")])).toBe(true);
    expect(hasIncompleteNumericRow([numRow("abc")])).toBe(true);
  });

  it("does not hold on a complete scalar value or an empty one", () => {
    expect(hasIncompleteNumericRow([numRow("5")])).toBe(false);
    expect(hasIncompleteNumericRow([numRow("-3.2")])).toBe(false);
    expect(hasIncompleteNumericRow([numRow("")])).toBe(false);
  });

  it("holds on a half-filled numeric range (one bound empty)", () => {
    expect(hasIncompleteNumericRow([numRow(["5", ""])])).toBe(true);
    expect(hasIncompleteNumericRow([numRow(["", "10"])])).toBe(true);
  });

  it("does not hold on a complete or fully-empty range", () => {
    expect(hasIncompleteNumericRow([numRow(["5", "10"])])).toBe(false);
    expect(hasIncompleteNumericRow([numRow(["", ""])])).toBe(false);
  });

  it("holds when a filled range bound is itself invalid", () => {
    expect(hasIncompleteNumericRow([numRow(["-", "10"])])).toBe(true);
  });

  it("ignores non-numeric rows", () => {
    expect(hasIncompleteNumericRow([{ fieldType: "string", value: "-" }])).toBe(
      false,
    );
    expect(
      hasIncompleteNumericRow([{ fieldType: "categorical", value: ["a"] }]),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// serializeFilterSet — canonical dedup projection (non-blocking review note):
// sets that produce the same API query serialize identically regardless of
// key order or display-only fields, so an identical set never refires.
// ---------------------------------------------------------------------------
describe("serializeFilterSet", () => {
  it("treats null and empty as the same empty set", () => {
    expect(serializeFilterSet(null)).toBe(serializeFilterSet([]));
  });

  it("ignores key order and display-only fields", () => {
    const a = [
      { field: "latency", operator: "greater_than", value: "5", fieldName: "Latency" },
    ];
    const b = [
      { value: "5", operator: "greater_than", field: "latency", fieldCategory: "system" },
    ];
    expect(serializeFilterSet(a)).toBe(serializeFilterSet(b));
  });

  it("distinguishes a changed value", () => {
    const a = [{ field: "latency", operator: "greater_than", value: "5" }];
    const b = [{ field: "latency", operator: "greater_than", value: "7" }];
    expect(serializeFilterSet(a)).not.toBe(serializeFilterSet(b));
  });
});

// ---------------------------------------------------------------------------
// Component behavior — debounced auto-apply, numeric hold, flush-on-close.
// ---------------------------------------------------------------------------
describe("TraceFilterPanel auto-apply behavior", () => {
  const NUMERIC_PROP = {
    id: "latency",
    name: "Latency",
    category: "system",
    type: "number",
  };

  const renderPanel = (currentFilters, { open = true } = {}) => {
    const onApply = vi.fn();
    const onClose = vi.fn();
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const ui = (props) => (
      <QueryClientProvider client={queryClient}>
        <TraceFilterPanel
          anchorEl={anchorEl}
          open={open}
          onClose={onClose}
          onApply={onApply}
          currentFilters={currentFilters}
          properties={[NUMERIC_PROP]}
          showQueryTab={false}
          {...props}
        />
      </QueryClientProvider>
    );
    const utils = render(ui());
    return { onApply, onClose, anchorEl, rerender: () => utils.rerender(ui({ open: false })), utils };
  };

  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("applies a changed value after the 350ms debounce", () => {
    const { onApply, utils } = renderPanel([
      { field: "latency", fieldType: "number", operator: "greater_than", value: "5" },
    ]);
    // seeded on open — no apply yet
    expect(onApply).not.toHaveBeenCalled();

    const input = utils.getByDisplayValue("5");
    act(() => {
      fireEvent.change(input, { target: { value: "7" } });
    });
    act(() => {
      vi.advanceTimersByTime(400);
    });

    expect(onApply).toHaveBeenCalledTimes(1);
    expect(serializeFilterSet(onApply.mock.calls[0][0])).toContain("7");
  });

  it("seeds last-applied on open so existing filters do not refire", () => {
    const { onApply } = renderPanel([
      { field: "latency", fieldType: "number", operator: "greater_than", value: "5" },
    ]);
    // Opening with an already-applied filter must not fire a redundant apply,
    // even after the debounce window elapses (seed-on-open dedup).
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(onApply).not.toHaveBeenCalled();
  });

  it("holds auto-apply while a partial/invalid numeric is typed", () => {
    const { onApply, utils } = renderPanel([
      { field: "latency", fieldType: "number", operator: "greater_than", value: "5" },
    ]);
    const input = utils.getByDisplayValue("5");

    act(() => {
      fireEvent.change(input, { target: { value: "-" } });
    });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(onApply).not.toHaveBeenCalled();

    // completing the value releases the hold
    act(() => {
      fireEvent.change(input, { target: { value: "-8" } });
    });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(serializeFilterSet(onApply.mock.calls[0][0])).toContain("-8");
  });

  it("holds when one bound of an applied range is cleared (no drop/refire)", () => {
    const { onApply, utils } = renderPanel([
      { field: "latency", fieldType: "number", operator: "between", value: ["5", "10"] },
    ]);
    const maxInput = utils.getByDisplayValue("10");

    act(() => {
      fireEvent.change(maxInput, { target: { value: "" } });
    });
    act(() => {
      vi.advanceTimersByTime(400);
    });

    // half-filled → held, so the previously-applied [5,10] is not dropped
    expect(onApply).not.toHaveBeenCalled();
  });

  it("flushes a pending value immediately when the popover closes", () => {
    const { onApply, utils } = renderPanel([
      { field: "latency", fieldType: "number", operator: "greater_than", value: "5" },
    ]);
    const input = utils.getByDisplayValue("5");

    act(() => {
      fireEvent.change(input, { target: { value: "9" } });
    });
    // close before the debounce fires
    act(() => {
      utils.rerender(
        <QueryClientProvider client={new QueryClient()}>
          <TraceFilterPanel
            anchorEl={document.createElement("button")}
            open={false}
            onClose={vi.fn()}
            onApply={onApply}
            currentFilters={[
              { field: "latency", fieldType: "number", operator: "greater_than", value: "5" },
            ]}
            properties={[NUMERIC_PROP]}
            showQueryTab={false}
          />
        </QueryClientProvider>,
      );
    });

    expect(onApply).toHaveBeenCalledTimes(1);
    expect(serializeFilterSet(onApply.mock.calls[0][0])).toContain("9");
  });
});

// ---------------------------------------------------------------------------
// Query tab — real timers (MUI Autocomplete's internal timing fights fake
// timers). Covers the debounced auto-apply of a committed token and, most
// importantly, flush-on-close of a typed-but-uncommitted token (Blocking 2).
// ---------------------------------------------------------------------------
describe("TraceFilterPanel Query tab", () => {
  const STRING_PROP = {
    id: "status",
    name: "Status",
    category: "system",
    type: "string",
  };

  const renderQueryPanel = () => {
    const onApply = vi.fn();
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const el = (open) => (
      <QueryClientProvider client={queryClient}>
        <TraceFilterPanel
          anchorEl={anchorEl}
          open={open}
          onClose={vi.fn()}
          onApply={onApply}
          currentFilters={[]}
          properties={[STRING_PROP]}
          showQueryTab
        />
      </QueryClientProvider>
    );
    const utils = render(el(true));
    return { onApply, utils, close: () => utils.rerender(el(false)) };
  };

  // Drive the phase machine: field -> operator, syncing on the placeholder
  // change so we don't race the dropdown-reopen timeout between phases.
  const selectFromDropdown = async (utils, typed, nextPlaceholder) => {
    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: typed } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(utils.getByRole("combobox")).toHaveAttribute(
        "placeholder",
        nextPlaceholder,
      ),
    );
  };

  const buildStatusContains = async (utils, value) => {
    fireEvent.click(utils.getByText("Query"));
    await selectFromDropdown(utils, "Status", "pick operator...");
    await selectFromDropdown(utils, "contains", "type or pick value...");
    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value } }); // value typed, uncommitted
    return input;
  };

  it("auto-applies a committed query token after the debounce", async () => {
    const { onApply, utils } = renderQueryPanel();
    const input = await buildStatusContains(utils, "prod");
    fireEvent.keyDown(input, { key: "Enter" }); // commit the token
    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(serializeFilterSet(onApply.mock.calls.at(-1)[0])).toContain("prod");
  });

  it("flushes a typed-but-uncommitted query token on close (Blocking 2)", async () => {
    const { onApply, utils, close } = renderQueryPanel();
    await buildStatusContains(utils, "staging"); // typed but NOT committed
    await act(async () => {
      close();
    });
    await waitFor(() => expect(onApply).toHaveBeenCalled());
    expect(serializeFilterSet(onApply.mock.calls.at(-1)[0])).toContain(
      "staging",
    );
  });
});
