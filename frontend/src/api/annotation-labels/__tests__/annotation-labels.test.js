import React from "react";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, it, expect, vi } from "vitest";
import {
  annotationLabelEndpoints,
  annotationLabelKeys,
  useInfiniteAnnotationLabelsList,
} from "../annotation-labels";

const { mockListLabels } = vi.hoisted(() => ({
  mockListLabels: vi.fn(),
}));

vi.mock("src/generated/api-contracts/api", () => ({
  modelHubAnnotationsLabelsCreate: vi.fn(),
  modelHubAnnotationsLabelsDelete: vi.fn(),
  modelHubAnnotationsLabelsList: mockListLabels,
  modelHubAnnotationsLabelsRestore: vi.fn(),
  modelHubAnnotationsLabelsUpdate: vi.fn(),
}));

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);
  Wrapper.propTypes = { children: PropTypes.node };
  Wrapper.queryClient = queryClient;
  return Wrapper;
};

const labels = (start, count) =>
  Array.from({ length: count }, (_, index) => ({
    id: `label-${start + index}`,
    name: `Label ${start + index}`,
    type: "text",
  }));

describe("Annotation Labels API", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("endpoints", () => {
    it("has correct list endpoint", () => {
      expect(annotationLabelEndpoints.list).toBe(
        "/model-hub/annotations-labels/",
      );
    });

    it("has correct create endpoint", () => {
      expect(annotationLabelEndpoints.create).toBe(
        "/model-hub/annotations-labels/",
      );
    });

    it("generates correct detail endpoint", () => {
      expect(annotationLabelEndpoints.detail("abc-123")).toBe(
        "/model-hub/annotations-labels/abc-123/",
      );
    });

    it("generates correct restore endpoint", () => {
      expect(annotationLabelEndpoints.restore("abc-123")).toBe(
        "/model-hub/annotations-labels/abc-123/restore/",
      );
    });
  });

  describe("query keys", () => {
    it("has correct all key", () => {
      expect(annotationLabelKeys.all).toEqual(["annotation-labels"]);
    });

    it("generates list key with filters", () => {
      const filters = { type: "categorical", page: 1 };
      expect(annotationLabelKeys.list(filters)).toEqual([
        "annotation-labels",
        "list",
        filters,
      ]);
    });

    it("generates detail key", () => {
      expect(annotationLabelKeys.detail("abc-123")).toEqual([
        "annotation-labels",
        "detail",
        "abc-123",
      ]);
    });
  });

  it("searches and accumulates every server page beyond the first 100 labels", async () => {
    mockListLabels
      .mockResolvedValueOnce({
        data: { count: 120, next: "?page=2", results: labels(0, 50) },
      })
      .mockResolvedValueOnce({
        data: { count: 120, next: "?page=3", results: labels(50, 50) },
      })
      .mockResolvedValueOnce({
        data: { count: 120, next: null, results: labels(100, 20) },
      });

    const { result } = renderHook(
      () => useInfiniteAnnotationLabelsList({ search: "priority", limit: 50 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.data?.results).toHaveLength(50));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.data?.results).toHaveLength(100));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.data?.results).toHaveLength(120));

    expect(mockListLabels).toHaveBeenNthCalledWith(
      1,
      { search: "priority", page: 1, limit: 50 },
      expect.objectContaining({ signal: expect.anything() }),
    );
    expect(mockListLabels).toHaveBeenNthCalledWith(
      2,
      { search: "priority", page: 2, limit: 50 },
      expect.objectContaining({ signal: expect.anything() }),
    );
    expect(mockListLabels).toHaveBeenNthCalledWith(
      3,
      { search: "priority", page: 3, limit: 50 },
      expect.objectContaining({ signal: expect.anything() }),
    );
    expect(result.current.hasNextPage).toBe(false);
  });

  it("treats the server next cursor as authoritative even when count is stale", async () => {
    mockListLabels.mockResolvedValueOnce({
      data: { count: 500, next: null, results: labels(0, 50) },
    });

    const { result } = renderHook(
      () => useInfiniteAnnotationLabelsList({ limit: 50 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.data?.results).toHaveLength(50));
    expect(result.current.hasNextPage).toBe(false);
  });

  it("stops on an empty page even if a malformed response includes next", async () => {
    mockListLabels.mockResolvedValueOnce({
      data: { count: 500, next: "?page=2", results: [] },
    });

    const { result } = renderHook(
      () => useInfiniteAnnotationLabelsList({ limit: 50 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(false);
  });

  it("marks list failures handled and aborts a stale search request", async () => {
    const pendingRequests = [];
    mockListLabels.mockImplementation(
      (params, { signal }) =>
        new Promise((resolve, reject) => {
          pendingRequests.push({ params, signal, resolve });
          signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    const wrapper = createWrapper();
    const { rerender, unmount } = renderHook(
      ({ search }) => useInfiniteAnnotationLabelsList({ search, limit: 50 }),
      { wrapper, initialProps: { search: "pri" } },
    );

    await waitFor(() => expect(pendingRequests).toHaveLength(1));
    const firstSignal = pendingRequests[0].signal;
    rerender({ search: "priority" });

    await waitFor(() => expect(pendingRequests).toHaveLength(2));
    expect(firstSignal.aborted).toBe(true);
    expect(pendingRequests[1].params.search).toBe("priority");
    expect(
      wrapper.queryClient.getQueryCache().getAll().at(-1)?.options.meta,
    ).toEqual({ errorHandled: true });

    pendingRequests[1].resolve({
      data: { count: 0, next: null, results: [] },
    });
    unmount();
  });
});
