import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";

const panelProps = vi.hoisted(() => vi.fn());
vi.mock("../TraceFilterPanel", () => ({ default: (props) => { panelProps(props); return null; } }));
vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("../BulkActionsBar", () => ({ default: () => null }));
vi.mock("../InstantEval/EvaluateMenu", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../tabStore", () => ({ useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }) }));

const searchLeaf = {
  column_id: "input_output",
  filter_config: { col_type: "SYSTEM_METRIC", filter_type: "text", filter_op: "contains", filter_value: "saved" },
};
const ordinaryLeaf = {
  column_id: "model",
  filter_config: { col_type: "SYSTEM_METRIC", filter_type: "text", filter_op: "equals", filter_value: "model-a" },
};
const toolbar = (props = {}) => <ObserveToolbar inline tab="trace" inputOutputSearch="" onInputOutputSearchChange={vi.fn()} {...props} />;
const search = () => screen.getByRole("textbox", { name: "Search input or output" });
const tick = () => act(() => vi.advanceTimersByTime(350));

beforeEach(() => { vi.useFakeTimers(); panelProps.mockClear(); });
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

describe("Observe full input/output search", () => {
  it.each(["trace", "spans"])("%s debounces a literal search and clears immediately", (tab) => {
    const changed = vi.fn();
    render(toolbar({ tab, onInputOutputSearchChange: changed }));
    fireEvent.change(search(), { target: { value: "a" } });
    fireEvent.change(search(), { target: { value: "É_%'\\literal" } });
    expect(changed).not.toHaveBeenCalled();
    tick();
    expect(changed).toHaveBeenCalledTimes(1);
    expect(changed).toHaveBeenLastCalledWith("É_%'\\literal");
    fireEvent.click(screen.getByRole("button", { name: "Clear input or output search" }));
    expect(changed).toHaveBeenLastCalledWith("");
    tick();
    expect(changed).toHaveBeenCalledTimes(2);
  });

  it("restores committed search and cancels the old draft on a saved-view change", () => {
    const changed = vi.fn();
    const { rerender } = render(toolbar({ inputOutputSearch: "saved", onInputOutputSearchChange: changed }));
    expect(search()).toHaveValue("saved");
    fireEvent.change(search(), { target: { value: "pending" } });
    rerender(toolbar({ inputOutputSearch: "restored", onInputOutputSearchChange: changed }));
    tick();
    expect(search()).toHaveValue("restored");
    expect(changed).not.toHaveBeenCalled();
  });

  it("keeps focus after a committed search and cancels drafts on tab changes", () => {
    const changed = vi.fn();
    const { rerender } = render(toolbar({ onInputOutputSearchChange: changed }));
    const input = search();
    input.focus();
    fireEvent.change(input, { target: { value: "committed" } });
    tick();
    rerender(toolbar({ inputOutputSearch: "committed", onInputOutputSearchChange: changed }));
    expect(search()).toBe(input);
    expect(document.activeElement).toBe(input);
    changed.mockClear();
    fireEvent.change(input, { target: { value: "pending" } });
    rerender(toolbar({ tab: "spans", inputOutputSearch: "committed", onInputOutputSearchChange: changed }));
    tick();
    expect(search()).toHaveValue("committed");
    expect(changed).not.toHaveBeenCalled();
  });

  it("bounds UTF-8 bytes rather than characters and does not send invalid drafts", () => {
    const changed = vi.fn();
    render(toolbar({ onInputOutputSearchChange: changed }));
    fireEvent.change(search(), { target: { value: "é".repeat(513) } });
    tick();
    expect(screen.getByText("Use 1,024 UTF-8 bytes or fewer.")).toBeInTheDocument();
    expect(changed).not.toHaveBeenCalled();
    fireEvent.change(search(), { target: { value: "é".repeat(512) } });
    tick();
    expect(changed).toHaveBeenLastCalledWith("é".repeat(512));
    fireEvent.change(search(), { target: { value: "   " } });
    tick();
    expect(changed).toHaveBeenLastCalledWith("");
  });

  it.each([{ mode: "sessions" }, { mode: "users" }, { isSimulator: true }, { onInputOutputSearchChange: undefined }])("does not offer unsupported search on %j", (props) => {
    render(toolbar(props));
    expect(screen.queryByRole("textbox", { name: "Search input or output" })).not.toBeInTheDocument();
  });

  it("keeps the reserved search separate from ordinary panel edits, including removal of the last row", () => {
    const apply = vi.fn();
    const clear = vi.fn();
    render(toolbar({ graphFilters: [searchLeaf, ordinaryLeaf], onApplyExtraFilters: apply, onClearExtraFilters: clear }));
    const panel = panelProps.mock.calls.at(-1)[0];
    expect(panel.currentFilters).toHaveLength(1);
    expect(panel.currentFilters[0].field).toBe("model");
    act(() => panel.onApply(panel.currentFilters));
    expect(apply.mock.calls.at(-1)[0]).toEqual(expect.arrayContaining([searchLeaf]));
    expect(apply.mock.calls.at(-1)[0].filter((leaf) => leaf.column_id === "input_output")).toHaveLength(1);
    act(() => panel.onApply([]));
    expect(apply).toHaveBeenLastCalledWith([searchLeaf]);
    expect(clear).not.toHaveBeenCalled();
    // Auto-apply also sends null when the final ordinary row is removed.
    act(() => panel.onApply(null));
    expect(apply).toHaveBeenLastCalledWith([searchLeaf]);
    expect(clear).not.toHaveBeenCalled();
    act(() => panel.onClear());
    expect(clear).toHaveBeenCalledOnce();
  });
});
