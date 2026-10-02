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

  it("keeps a server-confirmed long refresh in a neutral loading state", () => {
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

  it("presents exhausted client polling as paused instead of failed", () => {
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
      pollingPaused: true,
    });

    expect(state.data).toBeUndefined();
    expect(state.isLoading).toBe(false);
    expect(state.isError).toBe(false);
    expect(state.pollingPaused).toBe(true);
  });

  it("does not let a paused marker mask a true terminal failure", () => {
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
      isError: true,
      pollingPaused: true,
    });

    expect(state.isLoading).toBe(false);
    expect(state.isError).toBe(true);
    expect(state.pollingPaused).toBe(false);
  });

  it("keeps a cached exact graph visible with an explicit paused marker", () => {
    const previousExactData = {
      nodes: [{ id: "agent:a" }],
      edges: [],
      path_edges: [],
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      query_refreshing: false,
      query_refresh_failed: false,
    };
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
      previousExactData,
      isLoading: false,
      isError: false,
      pollingPaused: true,
    });

    expect(state.data).toBe(previousExactData);
    expect(state.isLoading).toBe(false);
    expect(state.isError).toBe(false);
    expect(state.pollingPaused).toBe(true);
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

  it("keeps a prior exact graph visible when a refresh poll fails", () => {
    const data = {
      nodes: [{ id: "agent:a" }],
      edges: [],
      path_edges: [],
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      query_refreshing: true,
      query_refresh_failed: false,
    };
    const state = getAgentGraphPresentationState({
      data,
      isLoading: false,
      isError: true,
    });

    expect(state.data).toBe(data);
    expect(state.isLoading).toBe(false);
    expect(state.isError).toBe(false);
  });
});
