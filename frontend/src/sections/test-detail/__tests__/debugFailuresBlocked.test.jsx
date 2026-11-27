import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { kpisQueryOptions } from "src/hooks/useKpis";
import { useFixMyAgentBlocked } from "../common";
import { useTestExecutionStore } from "../states";

const mockKpis = vi.hoisted(() => ({ current: { agent_type: "voice" } }));

vi.mock("src/hooks/useKpis", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({ data: mockKpis.current, isPending: false }),
}));

const BLOCKED_REASON =
  "You need at least 15 connected calls to debug failures.";
const NOTHING_FAILED_REASON =
  "Nothing failed in this run, so there’s nothing to debug.";

// Decides for e1 with the given store, after one KPI read of `kpis` landed.
const decide = (store, kpis) => {
  mockKpis.current = kpis;
  const queryClient = new QueryClient();
  queryClient.setQueryData(kpisQueryOptions("e1").queryKey, kpis);
  useTestExecutionStore.setState(store);
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return renderHook(() => useFixMyAgentBlocked("e1"), { wrapper: Wrapper })
    .result.current;
};

// The grid read this status for e1, and the one KPI read landed after the
// grid saw e1 finish.
const run = (status, kpis) =>
  decide(
    {
      status,
      statusExecutionId: "e1",
      kpisUpdateCountAtFinish: 0,
      finalKpisFailed: false,
    },
    kpis,
  );

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

  it("is blocked on a completed run with 15 connected calls and none failing", () => {
    expect(
      run("completed", {
        agent_type: "voice",
        total_calls: 20,
        connected_calls: 15,
        failing_calls: 0,
      }),
    ).toEqual({ disabled: true, reason: NOTHING_FAILED_REASON });
  });

  it("is open on a completed run with 15 connected calls and one failing", () => {
    expect(
      run("completed", {
        agent_type: "voice",
        total_calls: 20,
        connected_calls: 15,
        failing_calls: 1,
      }),
    ).toEqual({ disabled: false, reason: "" });
  });

  it("names the call count first on a run with 14 connected calls and none failing", () => {
    expect(
      run("completed", {
        agent_type: "voice",
        total_calls: 20,
        connected_calls: 14,
        failing_calls: 0,
      }),
    ).toEqual({ disabled: true, reason: BLOCKED_REASON });
  });

  it.each([
    ["too few connected calls", 14, 1],
    ["nothing failing", 15, 0],
  ])(
    "opens the analysis a run with %s already has",
    (_, connected_calls, failing_calls) => {
      expect(
        run("completed", {
          agent_type: "voice",
          total_calls: 20,
          connected_calls,
          failing_calls,
          has_debug_failures_analysis: true,
        }),
      ).toEqual({ disabled: false, reason: "" });
    },
  );

  it.each([
    [14, 1, BLOCKED_REASON],
    [15, 0, NOTHING_FAILED_REASON],
  ])(
    "is blocked on %s connected and %s failing with no analysis",
    (connected_calls, failing_calls, reason) => {
      expect(
        run("completed", {
          agent_type: "voice",
          total_calls: 20,
          connected_calls,
          failing_calls,
          has_debug_failures_analysis: false,
        }),
      ).toEqual({ disabled: true, reason });
    },
  );

  it("does not block on a count it was never sent", () => {
    expect(run("completed", { agent_type: "voice", total_calls: 3 })).toEqual({
      disabled: false,
      reason: "",
    });
  });
});

describe("Debug failures while it checks", () => {
  const CHECKING = { disabled: true, reason: "" };
  const fifteen = { agent_type: "voice", total_calls: 20, connected_calls: 15 };

  it("waits for a status read for the execution on screen", () => {
    expect(
      decide(
        {
          status: "completed",
          statusExecutionId: "e0",
          kpisUpdateCountAtFinish: 0,
          finalKpisFailed: false,
        },
        fifteen,
      ),
    ).toEqual(CHECKING);
  });

  it("waits for a KPI read that lands after the run finished", () => {
    expect(
      decide(
        {
          status: "completed",
          statusExecutionId: "e1",
          kpisUpdateCountAtFinish: 1,
          finalKpisFailed: false,
        },
        fifteen,
      ),
    ).toEqual(CHECKING);
  });

  it("says why when the final KPI read failed", () => {
    expect(
      decide(
        {
          status: "completed",
          statusExecutionId: "e1",
          kpisUpdateCountAtFinish: 1,
          finalKpisFailed: true,
        },
        fifteen,
      ),
    ).toEqual({
      disabled: true,
      reason: "Couldn't load the call count. Refresh to try again.",
    });
  });
});
