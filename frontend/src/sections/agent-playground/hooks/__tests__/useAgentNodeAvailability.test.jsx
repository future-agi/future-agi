import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import useAgentNodeAvailability, {
  AGENT_NODE_AVAILABILITY,
} from "../useAgentNodeAvailability";

const queryState = {
  data: undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
};
const mockRefetch = vi.fn();

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetReferenceableGraphs: () => ({ ...queryState, refetch: mockRefetch }),
}));

vi.mock("../../store", () => ({
  useAgentPlaygroundStoreShallow: () => ({ currentAgent: { id: "agent-1" } }),
}));

const setQuery = (overrides) =>
  Object.assign(queryState, {
    data: undefined,
    isLoading: false,
    isFetching: false,
    isError: false,
    ...overrides,
  });

// TH-4549: availability is derived only from what the backend returned for the
// current user/context. Error, loading and empty are distinct, and "empty"
// never means "the organization has no agents".
describe("useAgentNodeAvailability (TH-4549)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setQuery();
  });

  it("reports loading until the referenceable list has resolved", () => {
    setQuery({ isLoading: true });
    const { result } = renderHook(() => useAgentNodeAvailability());
    expect(result.current.status).toBe(AGENT_NODE_AVAILABILITY.LOADING);
    expect(result.current.isReady).toBe(false);
  });

  it("treats an undefined payload as loading, not as empty", () => {
    setQuery({ data: undefined, isLoading: false });
    const { result } = renderHook(() => useAgentNodeAvailability());
    expect(result.current.status).toBe(AGENT_NODE_AVAILABILITY.LOADING);
  });

  it("reports empty when the backend returned no eligible agents", () => {
    setQuery({ data: [] });
    const { result } = renderHook(() => useAgentNodeAvailability());
    expect(result.current.status).toBe(AGENT_NODE_AVAILABILITY.EMPTY);
    expect(result.current.isReady).toBe(false);
  });

  it("reports ready when at least one eligible agent exists", () => {
    setQuery({ data: [{ id: "g1" }] });
    const { result } = renderHook(() => useAgentNodeAvailability());
    expect(result.current.status).toBe(AGENT_NODE_AVAILABILITY.READY);
    expect(result.current.isReady).toBe(true);
  });

  it("error outranks empty so a failed request is never shown as 'no agents'", () => {
    setQuery({ data: [], isError: true });
    const { result } = renderHook(() => useAgentNodeAvailability());
    expect(result.current.status).toBe(AGENT_NODE_AVAILABILITY.ERROR);
  });

  it("refresh resolves to the fresh status from the refetched payload", async () => {
    setQuery({ data: [] });
    const { result } = renderHook(() => useAgentNodeAvailability());

    mockRefetch.mockResolvedValueOnce({ data: [{ id: "g1" }] });
    let next;
    await act(async () => {
      next = await result.current.refresh();
    });
    expect(next).toBe(AGENT_NODE_AVAILABILITY.READY);

    mockRefetch.mockResolvedValueOnce({ data: [] });
    await act(async () => {
      next = await result.current.refresh();
    });
    expect(next).toBe(AGENT_NODE_AVAILABILITY.EMPTY);

    mockRefetch.mockResolvedValueOnce({ error: new Error("403") });
    await act(async () => {
      next = await result.current.refresh();
    });
    expect(next).toBe(AGENT_NODE_AVAILABILITY.ERROR);
  });
});
