// HO results R7 review fix: the leaderboard's run-metric read asks only
// once the board's runs are known, asks again when a run's numbers move or
// a run joins, and keeps its last answer meanwhile (the column never drops
// out while the next answer loads).
import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useLeaderboardRunMetrics } from "../useLeaderboardRunMetrics";
import { runsStamp } from "../leaderboardRunMetrics";

const reads = [];
let reply = null;
vi.mock("src/utils/axios", () => ({
  default: {
    get: (path, opts) => {
      reads.push({ path, runs: opts?.params?.runs ?? null });
      return reply(opts?.params?.runs ?? "");
    },
  },
  endpoints: {},
}));
vi.mock("src/sections/common/Analysis/workbench/w5Api", () => ({
  w5Path: (name, id) => `/model-hub/datasets/${id}/derived-metrics/`,
}));

const metric = { id: "m1", name: "ship_score", formula: "tone" };
const answer = (runs, value) => ({
  data: {
    result: {
      metrics: [metric],
      values: Object.fromEntries(
        String(runs)
          .split(",")
          .filter(Boolean)
          .map((id) => [id, { m1: { value, parts: {} } }]),
      ),
    },
  },
});
const run = (id, total, status = "Running") => ({
  run_id: id,
  status,
  success_rate: { passed: total, total },
});

const wrapper = ({ children }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

describe("useLeaderboardRunMetrics", () => {
  beforeEach(() => {
    reads.length = 0;
    reply = (runs) => Promise.resolve(answer(runs, 0.5));
  });

  it("asks nothing before the board has runs", async () => {
    const { result } = renderHook(() => useLeaderboardRunMetrics("d1", []), {
      wrapper,
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(reads).toEqual([]);
    expect(result.current).toBeUndefined();
  });

  it("reads the runs' values, again when their numbers move, keeping the last answer meanwhile", async () => {
    let runs = [run("a", 3), run("b", 4)];
    const { result, rerender } = renderHook(
      () => useLeaderboardRunMetrics("d1", runs),
      { wrapper },
    );
    await waitFor(() => expect(result.current?.metrics).toEqual([metric]));
    expect(reads).toEqual([
      { path: "/model-hub/datasets/d1/derived-metrics/", runs: "a,b" },
    ]);
    expect(result.current.values.a.m1.value).toBe(0.5);

    // Same runs, same numbers (a poll): no new read.
    runs = [run("a", 3), run("b", 4)];
    rerender();
    expect(reads).toHaveLength(1);

    // A running run scored another row: read again; the column stays
    // with the last answer until the new one lands.
    let release;
    reply = (ids) =>
      new Promise((res) => {
        release = () => res(answer(ids, 0.9));
      });
    runs = [run("a", 4), run("b", 4)];
    rerender();
    await waitFor(() => expect(reads).toHaveLength(2));
    expect(result.current?.metrics).toEqual([metric]);
    expect(result.current.values.a.m1.value).toBe(0.5);
    release();
    await waitFor(() => expect(result.current.values.a.m1.value).toBe(0.9));
  });

  it("a run that joins the board keeps the column while its read loads", async () => {
    let runs = [run("a", 3, "Completed")];
    const { result, rerender } = renderHook(
      () => useLeaderboardRunMetrics("d1", runs),
      { wrapper },
    );
    await waitFor(() => expect(result.current?.metrics).toEqual([metric]));
    runs = [...runs, run("c", 0)];
    rerender();
    expect(result.current?.metrics).toEqual([metric]);
    await waitFor(() => expect(result.current.values.c).toBeTruthy());
    expect(reads.at(-1).runs).toBe("a,c");
  });

  it("the stamp moves only with what a formula reads", () => {
    const a = [run("a", 3), run("b", 4, "Completed")];
    expect(runsStamp(a)).toBe(
      runsStamp([run("a", 3), run("b", 4, "Completed")]),
    );
    expect(runsStamp(a)).not.toBe(
      runsStamp([run("a", 4), run("b", 4, "Completed")]),
    );
    expect(runsStamp(a)).not.toBe(
      runsStamp([run("a", 3, "Completed"), run("b", 4, "Completed")]),
    );
    // A waiting run's evals score more rows (its success rate is final).
    const scoring = (n) => ({
      ...run("a", 3, "WaitingForResults"),
      metrics: {
        aggregates: { "eval:tone": { n, pass_rate: 0.5 } },
        cost_usd: 0.01,
      },
    });
    expect(runsStamp([scoring(4)])).not.toBe(runsStamp([scoring(5)]));
    expect(runsStamp([scoring(4)])).toBe(runsStamp([scoring(4)]));
    expect(runsStamp(null)).toBe("");
  });
});
