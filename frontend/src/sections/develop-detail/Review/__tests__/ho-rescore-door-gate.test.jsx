// HO results R6 review fix: `Re-score N runs on vN` after Save corrections
// is offered only when the server's plan of that very request (the one
// re-score path's dry run) says it scores on vN. A server that would not
// (its plan names no dataset_version) gets no door and no toast action.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import useRescoreOnVersion from "../useRescoreOnVersion";

const planRescore = vi.fn();
const request = vi.fn();
vi.mock("src/sections/common/Runs/useRescore", () => ({
  planRescore: (...a) => planRescore(...a),
  useRescore: () => ({ request: (...a) => request(...a), dialog: null }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const r1 = {
  results: [
    {
      run_id: "a",
      label: "#1A",
      experiment_id: "x1",
      status: "Completed",
      dataset_version: { number: 5 },
      metric_keys: ["eval:correctness"],
    },
  ],
  metric_defs: [
    { key: "eval:correctness", name: "Correctness", user_eval_id: "u1" },
  ],
};
vi.mock("src/api/develop/r2", () => ({
  useRunConfigs: () => ({ data: r1 }),
}));

describe("Re-score on vN door", () => {
  beforeEach(() => {
    planRescore.mockReset();
    request.mockReset().mockResolvedValue({ cancelled: true });
  });

  it("is absent when the server's plan does not score on vN", async () => {
    planRescore.mockResolvedValue({
      error: true,
      reason:
        "The server did not plan this re-score on v6, so nothing was sent.",
    });
    const { result } = renderHook(() => useRescoreOnVersion("d1"));
    let out;
    await act(async () => {
      out = await result.current.offer(6);
    });
    expect(planRescore).toHaveBeenCalledTimes(1);
    const ask = planRescore.mock.calls[0][0];
    expect(ask.datasetVersion).toBe(6);
    expect(ask.runs.map((r) => r.run_id)).toEqual(["a"]);
    expect(ask.evalIdsOf()).toEqual(["u1"]);
    expect(out).toEqual({});
    expect(result.current.line).toBeNull();
  });

  it("is absent when the plan read fails", async () => {
    planRescore.mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useRescoreOnVersion("d1"));
    let out;
    await act(async () => {
      out = await result.current.offer(6);
    });
    expect(out).toEqual({});
    expect(result.current.line).toBeNull();
  });

  it("shows the door and the toast action when the plan is vN's, and asks the same request", async () => {
    planRescore.mockResolvedValue({ dataset_version: 6, runs: [] });
    const { result } = renderHook(() => useRescoreOnVersion("d1"));
    let out;
    await act(async () => {
      out = await result.current.offer(6);
    });
    expect(typeof out.action).toBe("function");
    expect(result.current.line).not.toBeNull();
    await act(async () => {
      await result.current.line.props.children.props.onClick();
    });
    expect(request).toHaveBeenCalledWith(planRescore.mock.calls[0][0]);
  });

  it("asks nothing when no run is on an older version", async () => {
    const { result } = renderHook(() => useRescoreOnVersion("d1"));
    let out;
    await act(async () => {
      out = await result.current.offer(5);
    });
    expect(planRescore).not.toHaveBeenCalled();
    expect(out).toEqual({});
  });
});
