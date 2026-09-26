import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Real endpoints (so the path must exist in the generated contract); only the
// transport is stubbed.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

const { default: axios } = await import("src/utils/axios");
const { mapDebugAnalysis, useDebugAnalysis, withCallContext } = await import(
  "../debugAnalysis"
);

// Field shapes mirror the live `GET /simulate/test-executions/{id}/debug-analysis/`.
const way = (id, phrase, callIds) => ({
  id,
  title: `Title ${id}`,
  phrase,
  call_ids: callIds,
});
const RAW = {
  status: "completed",
  report: {
    grouping_status: "completed",
    recorded_at: new Date().toISOString(),
  },
  findings: [],
  summary: {
    measured_call_count: 49,
    broken_goal_count: 1,
    broken_call_count: 3,
    one_off_count: 1,
    excluded_call_ids: ["c7", "c8"],
    unanalyzed_call_ids: ["c9"],
  },
  goals: [
    {
      goal: "exact_greeting",
      label: "Opens the call with the exact mandatory greeting",
      criteria:
        'Applies when: the call opens.\nPass when: the agent says exactly "Hi there."\nFail when: it alters the wording.',
      broken_call_ids: ["c1", "c2", "c3"],
      tested_call_count: 49,
      ways: [
        way("g1", "prepends a recording notice to the greeting", ["c1", "c2"]),
        way("g2", "omits the word book", ["c3"]),
      ],
      unexplained_call_ids: [],
    },
  ],
  one_offs: [way("f9", "omitted price information", ["c4"])],
};

describe("mapDebugAnalysis", () => {
  it("defaults to not requested with nothing to show", () => {
    expect(mapDebugAnalysis(undefined)).toMatchObject({
      status: "not_requested",
      isWorking: false,
      summary: null,
      goals: [],
      oneOffs: [],
    });
  });

  it("carries the computed goals, ways and moments through unchanged", () => {
    const analysis = mapDebugAnalysis(RAW);

    expect(analysis.summary).toEqual({
      measuredCalls: 49,
      brokenGoals: 1,
      brokenCalls: 3,
      oneOffs: 1,
      excludedCallIds: ["c7", "c8"],
      unanalyzedCallIds: ["c9"],
    });
    expect(analysis.goals[0]).toMatchObject({
      goal: "exact_greeting",
      label: "Opens the call with the exact mandatory greeting",
      brokenCallIds: ["c1", "c2", "c3"],
      testedCalls: 49,
      expected: 'the agent says exactly "Hi there."',
    });
    expect(analysis.goals[0].ways.map((w) => w.callIds.length)).toEqual([2, 1]);
    expect(analysis.oneOffs[0]).toMatchObject({ id: "f9", callIds: ["c4"] });
  });

  it("carries why the diagnosis failed", () => {
    expect(
      mapDebugAnalysis({ status: "failed", error_message: "Omega timed out" }),
    ).toMatchObject({ status: "failed", errorMessage: "Omega timed out" });
    expect(mapDebugAnalysis(RAW).errorMessage).toBeNull();
  });

  it("promises regrouping only while grouping is still live", () => {
    const pending = {
      ...RAW,
      report: {
        grouping_status: "pending",
        recorded_at: new Date().toISOString(),
      },
    };
    const stale = {
      ...RAW,
      report: {
        grouping_status: "pending",
        recorded_at: "2020-01-01T00:00:00Z",
      },
    };
    expect(mapDebugAnalysis(pending).groupingPending).toBe(true);
    expect(mapDebugAnalysis(stale).groupingPending).toBe(false);
  });

  it("marks pending and running as working", () => {
    expect(mapDebugAnalysis({ status: "pending" }).isWorking).toBe(true);
    expect(mapDebugAnalysis({ status: "running" }).isWorking).toBe(true);
    expect(mapDebugAnalysis({ status: "completed" }).isWorking).toBe(false);
  });
});

describe("withCallContext", () => {
  it("replaces call ids in one-off prose with the call's scenario", () => {
    const id = "3f1b2c4d-1111-4222-8333-444455556666";
    const analysis = mapDebugAnalysis({
      ...RAW,
      one_offs: [
        {
          ...way("f9", "omitted price", [id]),
          title: `In call ${id} it never gave the fare.`,
        },
      ],
    });
    const view = withCallContext(analysis, [{ id, scenario: "Fare question" }]);
    expect(view.oneOffs[0].title).toBe(
      'In call "Fare question" it never gave the fare.',
    );
  });

  it("shortens an id it cannot resolve instead of leaking the full uuid", () => {
    const id = "3f1b2c4d-1111-4222-8333-444455556666";
    const analysis = mapDebugAnalysis({
      ...RAW,
      one_offs: [
        { ...way("f9", "omitted price", [id]), title: `Call ${id} ended.` },
      ],
    });
    expect(withCallContext(analysis, []).oneOffs[0].title).toBe(
      "Call 3f1b2c4d… ended.",
    );
  });
});

describe("useDebugAnalysis", () => {
  const wrapper = ({ children }) => (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {children}
    </QueryClientProvider>
  );
  wrapper.propTypes = { children: PropTypes.node };

  beforeEach(() => {
    axios.get.mockReset();
    axios.post.mockReset();
  });

  it("reads the execution's diagnosis", async () => {
    axios.get.mockResolvedValue({
      data: { status: "not_requested", findings: [] },
    });

    const { result } = renderHook(() => useDebugAnalysis("ex1"), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/test-executions/ex1/debug-analysis/",
    );
    expect(result.current.analysis.status).toBe("not_requested");
  });

  it("requests with an explicit empty body, which the route's contract requires", async () => {
    axios.get.mockResolvedValue({
      data: { status: "not_requested", findings: [] },
    });
    axios.post.mockResolvedValue({ data: { status: "pending", findings: [] } });

    const { result } = renderHook(() => useDebugAnalysis("ex1"), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => result.current.request());

    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/test-executions/ex1/debug-analysis/",
      {},
    );
    await waitFor(() => expect(result.current.analysis.status).toBe("pending"));
  });
});
