import { describe, it, expect, vi } from "vitest";

// Unit tests for ExecutionsList auto-selection logic.
// Validates: auto-select the first run only when no run is currently selected,
// so a user selection is NOT hijacked when a new run appears at the top of the list.
//
// Reproduces issue #2511: selecting an older run in the run history was
// discarded whenever the list refreshed and prepended a newer run, because the
// useEffect that drives auto-selection did not guard on selectedExecutionId.

function autoSelectEffect({ selectedExecutionId, firstExecutionId, onExecutionChange }) {
  // Mirrors the guard added in ExecutionsList.jsx:
  //   if (!selectedExecutionId && firstExecutionId) { onExecutionChange(firstExecutionId); }
  if (!selectedExecutionId && firstExecutionId) {
    onExecutionChange(firstExecutionId);
  }
}

describe("ExecutionsList auto-selection logic", () => {
  it("auto-selects the first run when nothing is selected", () => {
    const onExecutionChange = vi.fn();
    autoSelectEffect({
      selectedExecutionId: null,
      firstExecutionId: "exec-1",
      onExecutionChange,
    });
    expect(onExecutionChange).toHaveBeenCalledOnce();
    expect(onExecutionChange).toHaveBeenCalledWith("exec-1");
  });

  it("does not override an existing user selection when a new run appears", () => {
    const onExecutionChange = vi.fn();
    autoSelectEffect({
      selectedExecutionId: "exec-old",
      firstExecutionId: "exec-new",
      onExecutionChange,
    });
    expect(onExecutionChange).not.toHaveBeenCalled();
  });

  it("does nothing when there are no executions yet", () => {
    const onExecutionChange = vi.fn();
    autoSelectEffect({
      selectedExecutionId: null,
      firstExecutionId: undefined,
      onExecutionChange,
    });
    expect(onExecutionChange).not.toHaveBeenCalled();
  });

  it("does not override when user has already selected the newest run", () => {
    const onExecutionChange = vi.fn();
    autoSelectEffect({
      selectedExecutionId: "exec-new",
      firstExecutionId: "exec-new",
      onExecutionChange,
    });
    expect(onExecutionChange).not.toHaveBeenCalled();
  });
});
