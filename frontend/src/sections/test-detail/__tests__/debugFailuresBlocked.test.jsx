import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useFixMyAgentBlocked } from "../common";
import { useTestExecutionStore } from "../states";

const mockKpis = vi.hoisted(() => ({ current: { agent_type: "voice" } }));

vi.mock("src/hooks/useKpis", () => ({
  default: () => ({ data: mockKpis.current, isPending: false }),
}));

const BLOCKED_REASON =
  "You need at least 15 connected calls to debug failures.";

const run = (status, kpis) => {
  mockKpis.current = kpis;
  // The grid read this status for e1, and e1's final KPIs once it finished.
  useTestExecutionStore.setState({
    status,
    statusExecutionId: "e1",
    finalKpisExecutionId: "e1",
  });
  return renderHook(() => useFixMyAgentBlocked("e1")).result.current;
};

describe("Debug failures on a run still in progress", () => {
  it("says it opens once the run finishes", () => {
    expect(run("running", { agent_type: "voice" })).toEqual({
      disabled: true,
      reason:
        "The test run is still running. You can debug its failures once it finishes.",
    });
  });
});

describe("Debug failures on a finished run", () => {
  beforeEach(() => {
    mockKpis.current = { agent_type: "voice" };
  });

  it.each(["voice", "text"])(
    "is blocked on a completed %s run with 14 connected calls",
    (agent_type) => {
      expect(
        run("completed", { agent_type, total_calls: 20, connected_calls: 14 }),
      ).toEqual({ disabled: true, reason: BLOCKED_REASON });
    },
  );

  it.each(["voice", "text"])(
    "is open on a completed %s run with exactly 15 connected calls",
    (agent_type) => {
      expect(
        run("completed", { agent_type, total_calls: 20, connected_calls: 15 }),
      ).toEqual({ disabled: false, reason: "" });
    },
  );

  it("is blocked on a cancelled run with 14 connected calls", () => {
    expect(
      run("cancelled", {
        agent_type: "voice",
        total_calls: 20,
        connected_calls: 14,
      }),
    ).toEqual({ disabled: true, reason: BLOCKED_REASON });
  });

  it("is blocked on a failed text run with no connected calls", () => {
    expect(
      run("failed", {
        agent_type: "text",
        total_calls: 20,
        connected_calls: 0,
      }),
    ).toEqual({ disabled: true, reason: BLOCKED_REASON });
  });

  it("is open on a failed run with 15 connected calls", () => {
    expect(
      run("failed", {
        agent_type: "text",
        total_calls: 20,
        connected_calls: 15,
      }),
    ).toEqual({ disabled: false, reason: "" });
  });

  it("does not block on a count it was never sent", () => {
    expect(run("completed", { agent_type: "voice", total_calls: 3 })).toEqual({
      disabled: false,
      reason: "",
    });
  });
});

describe("Debug failures while it checks", () => {
  const CHECKING = { disabled: true, reason: "" };
  const decide = (store) => {
    mockKpis.current = {
      agent_type: "voice",
      total_calls: 20,
      connected_calls: 15,
    };
    useTestExecutionStore.setState(store);
    return renderHook(() => useFixMyAgentBlocked("e1")).result.current;
  };

  it("waits for a status read for the execution on screen", () => {
    expect(
      decide({
        status: "completed",
        statusExecutionId: "e0",
        finalKpisExecutionId: "e0",
      }),
    ).toEqual(CHECKING);
  });

  it("waits for the final KPI read of a finished run", () => {
    expect(
      decide({
        status: "completed",
        statusExecutionId: "e1",
        finalKpisExecutionId: null,
      }),
    ).toEqual(CHECKING);
  });
});
