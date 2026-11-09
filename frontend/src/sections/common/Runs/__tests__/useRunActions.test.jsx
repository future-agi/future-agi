import React from "react";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ snack: [], api: [] }));
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
      return Promise.resolve({ data: { status: true, result } });
    };
  return {
    stopRuns: ok("stop", { runs: [{ done: 12 }] }),
    resumeRuns: ok("resume", { runs: [{ pending: 36 }] }),
    rerunCells: ok("rerunCells", {}),
    rerunExperiments: ok("rerun", "Re-Run has started for the experiments."),
    uploadRunResults: ok("upload", { accepted: 20 }),
  };
});

import useRunActions from "../useRunActions";
import { useRunActionConfirmStore } from "src/sections/develop-detail/Experiment/runActionConfirm";

const run = {
  run_id: "RUN_9B",
  label: "#9B",
  experiment_id: "EXP_9",
  experiment_ordinal: 9,
  progress: { done: 12, total: 48, errors: 2 },
};
const wrapper = ({ children }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
);

describe("useRunActions", () => {
  beforeEach(() => {
    calls.snack.length = 0;
    calls.api.length = 0;
    useRunActionConfirmStore.setState({ hosts: [], request: null });
  });

  it("stops one run and says how many examples are kept", async () => {
    const { result } = renderHook(() => useRunActions(run), { wrapper });
    await act(() => result.current.stop());
    expect(calls.api[0]).toEqual(["stop", "EXP_9", ["RUN_9B"]]);
    expect(calls.snack[0]).toEqual([
      "Stopped #9B · 12 of 48 rows kept",
      "success",
    ]);
    expect(result.current.labels.resume).toBe("Resume · 36 left");
  });

  it("asks before stopping when a confirm host is mounted", async () => {
    useRunActionConfirmStore.setState({ hosts: [1], request: null });
    const { result } = renderHook(() => useRunActions(run), { wrapper });
    let pending;
    act(() => {
      pending = result.current.stop();
    });
    const req = useRunActionConfirmStore.getState().request;
    expect(req.title).toBe("Stop #9B?");
    expect(req.content).toMatch(/So far about 12 of 48 rows are done/);
    await act(async () => {
      req.resolve(false);
      await pending;
    });
    expect(calls.api).toEqual([]);
  });

  it("re-runs the experiment (re-run/) or only this run (rerun-cells)", async () => {
    const { result } = renderHook(() => useRunActions(run), { wrapper });
    await act(() => result.current.rerunAll());
    await act(() => result.current.rerunRun());
    expect(calls.api[0]).toEqual(["rerun", ["EXP_9"]]);
    expect(calls.api[1]).toEqual([
      "rerunCells",
      "EXP_9",
      { source_ids: ["RUN_9B"] },
    ]);
    expect(calls.snack.map((s) => s[0])).toEqual([
      "Re-running experiment #9",
      "Re-running #9B on 48 examples",
    ]);
  });

  it("resumes, retries failed rows, re-scores and simulates uploads", async () => {
    const { result } = renderHook(() => useRunActions(run), { wrapper });
    await act(() => result.current.resume());
    await act(() => result.current.rerunFailed());
    await act(() => result.current.rescore(["UEM_1"]));
    await act(() => result.current.simulateUpload());
    expect(calls.api.map((c) => c[0])).toEqual([
      "resume",
      "rerunCells",
      "rerunCells",
      "upload",
    ]);
    expect(calls.api[1][2]).toEqual({
      source_ids: ["RUN_9B"],
      failed_only: true,
    });
    expect(calls.api[2][2]).toEqual({ user_eval_metric_ids: ["UEM_1"] });
    expect(calls.snack.map((s) => s[0])).toEqual([
      "#9B resumed · 36 rows left",
      "Retrying 2 failed examples of #9B",
      "Re-scoring #9B",
      "#9B · 20 results received",
    ]);
  });
});
