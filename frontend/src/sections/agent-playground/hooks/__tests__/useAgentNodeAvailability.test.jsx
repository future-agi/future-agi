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
  error: null,
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
    error: null,
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
    setQuery({ data: [], isError: true, error: { response: { status: 500 } } });
    const { result } = renderHook(() => useAgentNodeAvailability());
    expect(result.current.status).toBe(AGENT_NODE_AVAILABILITY.ERROR);
  });

  it("classifies the production axios-interceptor shape { statusCode } (AC-05): 403 → forbidden, 404 → not_found", () => {
    // src/utils/axios rejects `{ ...body, statusCode, transportCode }` — there is
    // no `response` on the error that reaches react-query consumers.
    setQuery({
      isError: true,
      error: {
        detail: "Forbidden",
        statusCode: 403,
        transportCode: "ERR_BAD_REQUEST",
      },
    });
    expect(
      renderHook(() => useAgentNodeAvailability()).result.current.status,
    ).toBe(AGENT_NODE_AVAILABILITY.FORBIDDEN);
    setQuery({
      isError: true,
      error: {
        detail: "Not found",
        statusCode: 404,
        transportCode: "ERR_BAD_REQUEST",
      },
    });
    expect(
      renderHook(() => useAgentNodeAvailability()).result.current.status,
    ).toBe(AGENT_NODE_AVAILABILITY.NOT_FOUND);
    setQuery({
      isError: true,
      error: {
        message: "Something went wrong",
        statusCode: 500,
        transportCode: "ERR_BAD_RESPONSE",
      },
    });
    expect(
      renderHook(() => useAgentNodeAvailability()).result.current.status,
    ).toBe(AGENT_NODE_AVAILABILITY.ERROR);
    setQuery({
      isError: true,
      error: {
        message: "Network Error",
        statusCode: undefined,
        transportCode: "ERR_NETWORK",
      },
    });
    expect(
      renderHook(() => useAgentNodeAvailability()).result.current.status,
    ).toBe(AGENT_NODE_AVAILABILITY.ERROR);
  });

  it("also accepts raw axios `response.status` and plain Error shapes", () => {
    setQuery({ isError: true, error: { response: { status: 403 } } });
    expect(
      renderHook(() => useAgentNodeAvailability()).result.current.status,
    ).toBe(AGENT_NODE_AVAILABILITY.FORBIDDEN);
    setQuery({ isError: true, error: { response: { status: 404 } } });
    expect(
      renderHook(() => useAgentNodeAvailability()).result.current.status,
    ).toBe(AGENT_NODE_AVAILABILITY.NOT_FOUND);
    setQuery({ isError: true, error: new Error("Network Error") });
    expect(
      renderHook(() => useAgentNodeAvailability()).result.current.status,
    ).toBe(AGENT_NODE_AVAILABILITY.ERROR);
  });

  it("treats a malformed (non-array) payload as error, not empty", () => {
    setQuery({ data: { graphs: [] } });
    const { result } = renderHook(() => useAgentNodeAvailability());
    expect(result.current.status).toBe(AGENT_NODE_AVAILABILITY.ERROR);
  });

  it("isRefreshing tracks only user-initiated refreshes, not background fetching", async () => {
    setQuery({ data: [], isFetching: true });
    const { result } = renderHook(() => useAgentNodeAvailability());
    expect(result.current.isRefreshing).toBe(false);

    let release;
    mockRefetch.mockImplementationOnce(
      () => new Promise((r) => (release = () => r({ data: [] }))),
    );
    let pending;
    act(() => {
      pending = result.current.refresh();
    });
    expect(result.current.isRefreshing).toBe(true);
    await act(async () => {
      release();
      await pending;
    });
    expect(result.current.isRefreshing).toBe(false);
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

    mockRefetch.mockResolvedValueOnce({
      error: {
        detail: "Forbidden",
        statusCode: 403,
        transportCode: "ERR_BAD_REQUEST",
      },
    });
    await act(async () => {
      next = await result.current.refresh();
    });
    expect(next).toBe(AGENT_NODE_AVAILABILITY.FORBIDDEN);

    mockRefetch.mockResolvedValueOnce({ error: new Error("Network Error") });
    await act(async () => {
      next = await result.current.refresh();
    });
    expect(next).toBe(AGENT_NODE_AVAILABILITY.ERROR);
  });
});
