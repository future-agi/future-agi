// the capability contract (DSX v2.1 gate): a "Test a fix" run that FAILED never leaves a
// green "finished" toast. The dock reads the run's real outcome through the
// one derivation (runStatus.runOutcomeKey): a run stamped Completed whose
// every row errored is Failed (error toast), one with some errored rows
// finished with errors (warning), and a partial run is terminal (polling
// stops) like any other ended run.
import { describe, expect, it } from "vitest";
import {
  iterateFinishToast,
  iterateOutcomeKey,
} from "src/sections/experiment-detail/iterate/iterateStore";
import { TERMINAL_STATUSES } from "src/sections/common/Runs/runStatus";

const FAILED = {
  run_id: "R9",
  status: "Completed",
  progress: { done: 6, total: 6, errors: 6 },
};
const PARTIAL = {
  run_id: "R9",
  status: "Completed",
  progress: { done: 6, total: 6, errors: 2 },
};
const OK = {
  run_id: "R9",
  status: "Completed",
  progress: { done: 6, total: 6, errors: 0 },
};

describe("the fix run's toast reads its real outcome", () => {
  it("a fix run whose every row errored is Failed, with an error toast", () => {
    const key = iterateOutcomeKey(FAILED, FAILED.status);
    expect(key).toBe("Failed");
    expect(TERMINAL_STATUSES).toContain(key);
    const t = iterateFinishToast("#3B", key, "0 better, 0 worse");
    expect(t.variant).toBe("error");
    expect(t.text).toBe("#3B failed");
    expect(t.text).not.toMatch(/finished|Completed/);
  });

  it("a partial fix run is terminal and finished with errors (warning)", () => {
    const key = iterateOutcomeKey(PARTIAL, PARTIAL.status);
    expect(key).toBe("PartialCompleted");
    expect(TERMINAL_STATUSES).toContain(key);
    const t = iterateFinishToast("#3B", key, "2 better, 1 worse");
    expect(t.variant).toBe("warning");
    expect(t.text).toBe("#3B finished with errors: 2 better, 1 worse");
  });

  it("only a clean Completed is a success; a stop is a warning", () => {
    expect(
      iterateFinishToast("#3B", iterateOutcomeKey(OK, OK.status)).variant,
    ).toBe("success");
    expect(
      iterateFinishToast("#3B", iterateOutcomeKey(null, "Cancelled")),
    ).toEqual({
      text: "#3B stopped before it finished",
      variant: "warning",
    });
    expect(iterateOutcomeKey(null, "Error")).toBe("Failed");
  });
});
