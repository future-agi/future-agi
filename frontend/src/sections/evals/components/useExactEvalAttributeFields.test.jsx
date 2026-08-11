import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    project: {
      spanAttributeKeys: () => "/api/traces/span-attribute-keys/",
    },
  },
}));

import {
  retainedAttributeFieldName,
  useExactEvalAttributeFields,
} from "./useExactEvalAttributeFields";

function retainedPage(keys, overrides = {}) {
  return {
    data: {
      result: keys.map((key) => ({ key, type: "string", count: 1 })),
      query_complete: true,
      query_status: "complete",
      browse_mode: "recent_suggestions",
      browse_status: "exhausted",
      has_more: false,
      next_cursor: null,
      ...overrides,
    },
  };
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

describe("useExactEvalAttributeFields", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockImplementation((_url, { params }) =>
      Promise.resolve(
        params.q ? retainedPage([]) : retainedPage(["retained_status"]),
      ),
    );
  });

  it.each([
    [
      "Span",
      " final_status ",
      "final_status",
      ["retained_status", "final_status"],
    ],
    [
      "traces",
      " spans.0.final_status ",
      "spans.0.final_status",
      ["spans.0.retained_status", "spans.0.final_status"],
    ],
  ])(
    "merges retained %s fields with a non-blocking retained exact search",
    async (rowType, search, expectedField, expectedFields) => {
      mocks.get.mockImplementation((_url, { params }) =>
        Promise.resolve(
          params.q
            ? retainedPage(["final_status"], {
                lookup_mode: "exact",
                exact_match: true,
              })
            : retainedPage(["retained_status"]),
        ),
      );

      const { result } = renderHook(
        () =>
          useExactEvalAttributeFields({
            projectId: "00000000-0000-4000-8000-000000000901",
            rowType,
            search,
          }),
        { wrapper: createWrapper() },
      );

      await waitFor(() => expect(result.current.data).toEqual(expectedFields));
      expect(result.current.isSuccess).toBe(true);
      expect(result.current.queryReadState).toBe("complete");
      expect(result.current.data).toContain(expectedField);
      expect(mocks.get).toHaveBeenCalledWith(
        "/api/traces/span-attribute-keys/",
        expect.objectContaining({
          signal: expect.any(AbortSignal),
          timeout: 35_000,
          params: {
            project_id: "00000000-0000-4000-8000-000000000901",
            page_size: 10,
          },
        }),
      );
      expect(mocks.get).toHaveBeenCalledWith(
        "/api/traces/span-attribute-keys/",
        expect.objectContaining({
          signal: expect.any(AbortSignal),
          timeout: 35_000,
          params: {
            project_id: "00000000-0000-4000-8000-000000000901",
            page_size: 10,
            q: "final_status",
          },
        }),
      );
    },
  );

  it("continues the retained cursor and de-duplicates fields across pages", async () => {
    mocks.get
      .mockResolvedValueOnce(
        retainedPage(["first", "duplicate"], {
          browse_status: "continuation",
          has_more: true,
          next_cursor: "retained-page-2",
        }),
      )
      .mockResolvedValueOnce(retainedPage(["duplicate", "older"]));

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "traces",
          search: "",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));

    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        params: {
          project_id: "project-synthetic",
          page_size: 10,
          cursor: "retained-page-2",
        },
      }),
    );
    expect(result.current.data).toEqual([
      "spans.0.first",
      "spans.0.duplicate",
      "spans.0.older",
    ]);
  });

  it("continues eval-field discovery after a resumable limit_reached batch", async () => {
    mocks.get
      .mockResolvedValueOnce(
        retainedPage(["recent"], {
          browse_status: "limit_reached",
          has_more: true,
          next_cursor: "next-bounded-batch",
        }),
      )
      .mockResolvedValueOnce(retainedPage(["older"]));

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "traces",
          search: "",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("next-bounded-batch");
    expect(result.current.data).toEqual(["spans.0.recent", "spans.0.older"]);
  });

  it("returns control after one bounded chunk and continues older checkpoints on request", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.cursor) {
        return Promise.resolve(
          retainedPage(["recent"], {
            browse_status: "continuation",
            has_more: true,
            next_cursor: "empty-1",
          }),
        );
      }
      const index = Number(params.cursor.slice("empty-".length));
      if (index <= 14) {
        return Promise.resolve(
          retainedPage([], {
            browse_status: "continuation",
            has_more: true,
            next_cursor: `empty-${index + 1}`,
          }),
        );
      }
      return Promise.resolve(retainedPage([]));
    });

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "traces",
          search: "",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(14));

    expect(result.current.hasNextPage).toBe(true);
    expect(result.current.isFetchingNextPage).toBe(false);
    expect(result.current.data).toEqual(["spans.0.recent"]);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(mocks.get).toHaveBeenCalledTimes(16);
    expect(result.current.data).toEqual(["spans.0.recent"]);
    expect(result.current.isError).toBe(false);
    expect(result.current.isFetchNextPageError).toBe(false);
  });

  it("retries a repeated cursor once, then terminalizes with prior fields intact", async () => {
    mocks.get
      .mockResolvedValueOnce(
        retainedPage(["recent"], {
          browse_status: "continuation",
          has_more: true,
          next_cursor: "same-cursor",
        }),
      )
      .mockResolvedValueOnce(
        retainedPage([], {
          browse_status: "continuation",
          has_more: true,
          next_cursor: "same-cursor",
        }),
      )
      .mockResolvedValueOnce(
        retainedPage(["recent"], {
          browse_status: "continuation",
          has_more: true,
          next_cursor: "same-cursor",
        }),
      )
      .mockResolvedValueOnce(
        retainedPage([], {
          browse_status: "continuation",
          has_more: true,
          next_cursor: "same-cursor",
        }),
      );

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "traces",
          search: "",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.queryReadState).toBe("degraded"));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.data).toEqual(["spans.0.recent"]);
    expect(result.current.hasNextPage).toBe(true);
    expect(result.current.isFetchNextPageError).toBe(true);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.cursorRetryExhausted).toBe(true));

    expect(mocks.get).toHaveBeenCalledTimes(4);
    expect(result.current.data).toEqual(["spans.0.recent"]);
    expect(result.current.hasNextPage).toBe(false);
    expect(result.current.isFetchNextPageError).toBe(false);

    await act(async () => result.current.fetchNextPage());
    expect(mocks.get).toHaveBeenCalledTimes(4);
  });

  it("continues exact search to an older key and stops at the terminal page", async () => {
    const exactRequests = [];
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.q) {
        return Promise.resolve(retainedPage(["recent_catalog"]));
      }

      exactRequests.push(params);
      if (!params.cursor) {
        return Promise.resolve(
          retainedPage([], {
            browse_status: "continuation",
            has_more: true,
            next_cursor: "exact-page-2",
            lookup_mode: "exact",
            exact_match: false,
          }),
        );
      }

      return Promise.resolve(
        retainedPage(["older_exact_key", "older_exact_key"], {
          browse_status: "exhausted",
          has_more: false,
          next_cursor: null,
          lookup_mode: "exact",
          exact_match: true,
        }),
      );
    });

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "spans",
          search: "older_exact_key",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.data).toEqual([
        "recent_catalog",
        "older_exact_key",
      ]),
    );

    expect(exactRequests).toEqual([
      expect.objectContaining({ q: "older_exact_key" }),
      expect.objectContaining({
        q: "older_exact_key",
        cursor: "exact-page-2",
      }),
    ]);
    expect(result.current.hasNextPage).toBe(false);

    const completedRequestCount = mocks.get.mock.calls.length;
    await act(async () => result.current.fetchNextPage());
    expect(mocks.get).toHaveBeenCalledTimes(completedRequestCount);
  });

  it("does not advance the unrelated retained catalog after an exact key is found", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.q) {
        return Promise.resolve(
          retainedPage(["final_status"], {
            lookup_mode: "exact",
            exact_match: true,
          }),
        );
      }
      if (params.cursor === "retained-page-2") {
        return Promise.resolve(retainedPage(["unrelated_older_key"]));
      }
      return Promise.resolve(
        retainedPage(["recent_catalog"], {
          browse_status: "continuation",
          has_more: true,
          next_cursor: "retained-page-2",
        }),
      );
    });

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "spans",
          search: "final_status",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.data).toEqual(["recent_catalog", "final_status"]),
    );
    expect(result.current.hasNextPage).toBe(false);
    const completedRequestCount = mocks.get.mock.calls.length;
    await act(async () => result.current.fetchNextPage());
    expect(mocks.get).toHaveBeenCalledTimes(completedRequestCount);
    expect(
      mocks.get.mock.calls.some(
        ([, options]) => options.params.cursor === "retained-page-2",
      ),
    ).toBe(false);
  });

  it("reuses retained pages while exact search changes", async () => {
    const exactRequests = [];
    mocks.get.mockImplementation((_url, options) => {
      if (!options.params.q) return Promise.resolve(retainedPage(["retained"]));
      return new Promise((resolve) => exactRequests.push({ options, resolve }));
    });

    const { result, rerender } = renderHook(
      ({ search }) =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "spans",
          search,
        }),
      {
        initialProps: { search: "final_status" },
        wrapper: createWrapper(),
      },
    );

    await waitFor(() => expect(exactRequests).toHaveLength(1));
    rerender({ search: "customer_outcome" });
    await waitFor(() => expect(exactRequests).toHaveLength(2));

    expect(
      mocks.get.mock.calls.filter(([, options]) => !options.params.q),
    ).toHaveLength(1);
    expect(exactRequests[0].options.signal.aborted).toBe(true);
    expect(exactRequests[1].options.params.q).toBe("customer_outcome");

    await act(async () => {
      exactRequests[0].resolve(retainedPage(["final_status"]));
      exactRequests[1].resolve(retainedPage(["customer_outcome"]));
    });
    await waitFor(() =>
      expect(result.current.data).toEqual(["retained", "customer_outcome"]),
    );
  });

  it("does not publish degraded state from the optional exact search", async () => {
    mocks.get.mockImplementation((_url, { params }) =>
      Promise.resolve(
        params.q
          ? retainedPage(["final_status"], {
              query_complete: false,
              query_status: "degraded",
              query_error_code: "read_budget_exceeded",
            })
          : retainedPage(["retained_status"]),
      ),
    );

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "spans",
          search: "final_status",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.data).toEqual(["retained_status", "final_status"]),
    );
    expect(result.current.queryReadState).toBe("complete");
    expect(result.current.isError).toBe(false);
  });

  it("keeps the mapping usable when the optional exact search fails", async () => {
    mocks.get.mockImplementation((_url, { params }) =>
      params.q
        ? Promise.reject(new Error("internal details"))
        : Promise.resolve(retainedPage(["retained_status"])),
    );

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "spans",
          search: "final_status",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(["retained_status"]);
    expect(result.current.isError).toBe(false);
    expect(result.current.queryReadState).toBe("complete");
  });

  it("does not block mapping while optional exact search is pending", async () => {
    mocks.get.mockImplementation((_url, { params }) =>
      params.q
        ? new Promise(() => {})
        : Promise.resolve(retainedPage(["retained_status"])),
    );

    const { result } = renderHook(
      () =>
        useExactEvalAttributeFields({
          projectId: "project-synthetic",
          rowType: "spans",
          search: "final_status",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.isFetching).toBe(false);
    expect(result.current.data).toEqual(["retained_status"]);
  });

  it.each(["sessions", "voiceCalls"])(
    "does not probe unsupported %s mappings",
    (rowType) => {
      renderHook(
        () =>
          useExactEvalAttributeFields({
            projectId: "project-synthetic",
            rowType,
            search: "final_status",
          }),
        { wrapper: createWrapper() },
      );

      expect(mocks.get).not.toHaveBeenCalled();
    },
  );

  it("maps retained keys to the resolver's canonical row paths", () => {
    expect(retainedAttributeFieldName("llm.model", "spans")).toBe("llm.model");
    expect(retainedAttributeFieldName("llm.model", "traces")).toBe(
      "spans.0.llm.model",
    );
    expect(retainedAttributeFieldName("", "traces")).toBeNull();
  });
});
