// options-truth L7b G4 (action:resume:cost-capped): a resume the server
// refuses (the run stopped at its cost cap) toasts the refusal, never a
// success with Undo; the Resume doors read the ledger.
import React from "react";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ snack: [], api: [], resume: null }));
vi.mock("notistack", () => ({
  enqueueSnackbar: (text, opts) => calls.snack.push([text, opts?.variant]),
  closeSnackbar: () => {},
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/api/develop/experiment-run-actions", () => {
  const ok =
    (name, result) =>
    (...args) => {
      calls.api.push([name, ...args]);
      return Promise.resolve({
        data: { status: true, result: calls[name] ?? result },
      });
    };
  return {
    stopRuns: ok("stop", { runs: [{ done: 12 }] }),
    resumeRuns: ok("resume", { runs: [{ pending: 36 }] }),
    rerunCells: ok("rerunCells", {}),
    rerunExperiments: ok("rerun", "ok"),
    uploadRunResults: ok("upload", { accepted: 20 }),
  };
});

import useRunActions from "../useRunActions";
import {
  canResumeRun,
  resumeOutcomeOf,
  stopLimitOf,
  stoppedAtCostCap,
} from "../runResume";
import { useRunActionConfirmStore } from "src/sections/develop-detail/Experiment/runActionConfirm";

const capped = {
  run_id: "RUN_9A",
  label: "#9A",
  experiment_id: "EXP_9",
  status: "Cancelled",
  status_detail: "Stopped at cost cap",
  progress: { done: 59, total: 248, errors: 0 },
  ledger: { stopped_by: "cost_cap", rows: { stopped: 189 } },
};
const byHand = {
  ...capped,
  run_id: "RUN_8A",
  label: "#8A",
  status_detail: "Stopped by Maya Chen",
  ledger: { stopped_by: "stopped", rows: { stopped: 152 } },
};
const wrapper = ({ children }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
);

describe("runResume (pure)", () => {
  it("reads the limit from the ledger, else the stop reason", () => {
    expect(stopLimitOf(capped)).toBe("cost_cap");
    expect(stopLimitOf(byHand)).toBe("stopped");
    expect(stopLimitOf({ ...capped, ledger: null })).toBe("cost_cap");
    expect(stopLimitOf({ ...capped, status: "Completed" })).toBeNull();
    expect(stoppedAtCostCap(capped)).toBe(true);
  });

  it("offers Resume only for a stopped run with rows left that no cap stopped", () => {
    expect(canResumeRun(capped)).toBe(false);
    expect(canResumeRun(byHand)).toBe(true);
    expect(
      canResumeRun({ ...byHand, progress: { done: 248, total: 248 } }),
    ).toBe(false);
    expect(canResumeRun({ ...byHand, status: "Running" })).toBe(false);
  });

  it("splits a resume response into resumed runs and refusals", () => {
    expect(
      resumeOutcomeOf({
        runs: [
          { run_id: "a", pending: 3 },
          { run_id: "b", error: "#9A stopped at its cost cap." },
        ],
      }),
    ).toEqual({
      resumed: [{ run_id: "a", pending: 3 }],
      refused: ["#9A stopped at its cost cap."],
    });
    expect(resumeOutcomeOf(null)).toEqual({ resumed: [], refused: [] });
  });
});

describe("useRunActions.resume reads the server's per-run answer", () => {
  beforeEach(() => {
    calls.snack.length = 0;
    calls.api.length = 0;
    calls.resume = null;
    useRunActionConfirmStore.setState({ hosts: [], request: null });
  });

  it("a refused run shows its message, never `resumed`", async () => {
    calls.resume = {
      message: "Resumed 1 run.",
      runs: [
        {
          run_id: "RUN_9A",
          error: "#9A stopped at its cost cap. Rerun it with a higher cap.",
        },
      ],
    };
    const { result } = renderHook(() => useRunActions(capped), { wrapper });
    let out;
    await act(async () => {
      out = await result.current.resume();
    });
    expect(out).toBeNull();
    expect(calls.snack).toEqual([
      ["#9A stopped at its cost cap. Rerun it with a higher cap.", "error"],
    ]);
    expect(result.current.canResume).toBe(false);
  });

  it("a resumed run toasts its pending rows", async () => {
    calls.resume = { runs: [{ run_id: "RUN_8A", pending: 152 }] };
    const { result } = renderHook(() => useRunActions(byHand), { wrapper });
    await act(() => result.current.resume());
    expect(calls.snack).toEqual([["#8A resumed · 152 rows left", "success"]]);
    expect(result.current.canResume).toBe(true);
  });
});
