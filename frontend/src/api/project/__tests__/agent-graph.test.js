import { describe, expect, it } from "vitest";
import { getAgentGraphPresentationState } from "../agent-graph";

describe("getAgentGraphPresentationState", () => {
  it("turns a terminal failed refresh into an error instead of an endless spinner", () => {
    const state = getAgentGraphPresentationState({
      data: {
        nodes: [],
        edges: [],
        path_edges: [],
        query_complete: false,
        query_status: "pending",
        query_sampled: false,
        query_refreshing: false,
        query_refresh_failed: true,
      },
      isLoading: false,
      isError: false,
    });

    expect(state).toEqual(
      expect.objectContaining({
        data: undefined,
        isLoading: false,
        isError: true,
        queryReadState: "pending",
      }),
    );
  });

  it("keeps a live pending refresh in loading state", () => {
    const state = getAgentGraphPresentationState({
      data: {
        nodes: [],
        edges: [],
        path_edges: [],
        query_complete: false,
        query_status: "pending",
        query_sampled: false,
        query_refreshing: true,
        query_refresh_failed: false,
      },
      isLoading: false,
      isError: false,
    });

    expect(state.isLoading).toBe(true);
    expect(state.isError).toBe(false);
  });

  it("turns a pending refresh that exceeds the client budget into a visible error", () => {
    const state = getAgentGraphPresentationState(
      {
        data: {
          nodes: [],
          edges: [],
          path_edges: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
          query_refresh_failed: false,
        },
        isLoading: false,
        isError: false,
      },
      { pendingTimedOut: true },
    );

    expect(state.isLoading).toBe(false);
    expect(state.isError).toBe(true);
  });

  it("does not keep loading when a pending poll request fails", () => {
    const state = getAgentGraphPresentationState({
      data: {
        nodes: [],
        edges: [],
        path_edges: [],
        query_complete: false,
        query_status: "pending",
        query_sampled: false,
        query_refreshing: true,
        query_refresh_failed: false,
      },
      isLoading: false,
      isError: true,
    });

    expect(state.isLoading).toBe(false);
    expect(state.isError).toBe(true);
  });

  it("presents a settled exact empty graph as data instead of loading", () => {
    const data = {
      nodes: [],
      edges: [],
      path_edges: [],
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      query_refreshing: false,
      query_refresh_failed: false,
    };
    const state = getAgentGraphPresentationState({
      data,
      isLoading: false,
      isError: false,
    });

    expect(state.data).toBe(data);
    expect(state.isLoading).toBe(false);
    expect(state.isError).toBe(false);
  });
});
