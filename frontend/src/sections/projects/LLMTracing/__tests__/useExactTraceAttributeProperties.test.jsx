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
  default: mocks,
  endpoints: {
    project: {
      spanAttributeKeys: () => "/api/traces/span-attribute-keys/",
    },
  },
}));

import {
  getAttributeKeyPageReadState,
  useExactTraceAttributeProperties,
} from "../useExactTraceAttributeProperties";

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

describe("useExactTraceAttributeProperties", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads ten retained keys first and de-duplicates cursor pages", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: [
            { key: "call.status", type: "string", count: 3 },
            { key: "final_status", type: "string", count: 2 },
          ],
          query_complete: true,
          query_status: "complete",
          browse_mode: "recent_suggestions",
          browse_status: "continuation",
          browse_limit: 224,
          has_more: true,
          next_cursor: "signed-page-2",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [
            { key: "final_status", type: "string", count: 1 },
            { key: "cost_cents", type: "number", count: 1 },
          ],
          query_complete: true,
          query_status: "complete",
          browse_mode: "recent_suggestions",
          browse_status: "exhausted",
          browse_limit: 224,
          has_more: false,
          next_cursor: null,
        },
      });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.get).toHaveBeenNthCalledWith(
      1,
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        timeout: 35_000,
        params: {
          project_id: "project-synthetic",
          page_size: 10,
        },
      }),
    );
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        timeout: 35_000,
        params: {
          project_id: "project-synthetic",
          page_size: 10,
          cursor: "signed-page-2",
        },
      }),
    );
    expect(result.current.data.map((item) => item.id)).toEqual([
      "call.status",
      "final_status",
      "cost_cents",
    ]);
    expect(result.current.hasNextPage).toBe(false);
    expect(result.current.queryReadState).toBe("complete");
    expect(result.current.browseStatus).toBe("exhausted");
    expect(result.current.browseLimit).toBe(224);
    expect(result.current.browseLimitReached).toBe(false);
  });

  it("returns control after one bounded chunk and continues older retained checkpoints on request", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.cursor) {
        return Promise.resolve({
          data: {
            result: [{ key: "recent.attribute", type: "string" }],
            browse_status: "continuation",
            has_more: true,
            next_cursor: "empty-1",
          },
        });
      }
      const index = Number(params.cursor.slice("empty-".length));
      if (index <= 14) {
        return Promise.resolve({
          data: {
            result: [],
            browse_status: "continuation",
            has_more: true,
            next_cursor: `empty-${index + 1}`,
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "older.attribute", type: "number" }],
          browse_status: "exhausted",
          has_more: false,
          next_cursor: null,
        },
      });
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(14));

    expect(result.current.hasNextPage).toBe(true);
    expect(result.current.isFetchingNextPage).toBe(false);
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "recent.attribute",
    ]);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(mocks.get).toHaveBeenCalledTimes(16);
    expect(result.current.isError).toBe(false);
    expect(result.current.isFetchNextPageError).toBe(false);
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "recent.attribute",
      "older.attribute",
    ]);
  });

  it("keeps prior keys and makes a repeated retained cursor degraded and retryable", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: [{ key: "recent.attribute", type: "string" }],
          browse_status: "continuation",
          has_more: true,
          next_cursor: "same-cursor",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [],
          browse_status: "continuation",
          has_more: true,
          next_cursor: "same-cursor",
        },
      });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.queryReadState).toBe("degraded"));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.hasNextPage).toBe(true);
    expect(result.current.isFetchNextPageError).toBe(true);
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "recent.attribute",
    ]);
  });

  it("keeps an unchanged exhausted catalog after a successful cached refetch", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: [{ key: "final_status", type: "string", count: 1 }],
        query_complete: true,
        query_status: "complete",
        browse_mode: "retained_catalog",
        browse_status: "exhausted",
        has_more: false,
        next_cursor: null,
      },
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.data.map(({ id }) => id)).toEqual(["final_status"]),
    );
    await act(async () => result.current.refetch());

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.data.map(({ id }) => id)).toEqual(["final_status"]);
    expect(result.current.queryReadState).toBe("complete");
    expect(result.current.browseStatus).toBe("exhausted");
    expect(result.current.hasNextPage).toBe(false);
  });

  it("uses endpoint-specific browse state instead of generic sampling state", () => {
    expect(
      getAttributeKeyPageReadState({
        query_complete: true,
        query_status: "complete",
        browse_mode: "recent_suggestions",
        browse_status: "limit_reached",
      }),
    ).toBe("complete");
    expect(
      getAttributeKeyPageReadState({
        query_complete: false,
        query_status: "degraded",
        browse_mode: "recent_suggestions",
        browse_status: "continuation",
      }),
    ).toBe("degraded");
  });

  it("treats a verified positive exact lookup as authoritative beyond browse", () => {
    expect(
      getAttributeKeyPageReadState(
        {
          result: [{ key: "older_exact_key", type: "string", count: 1 }],
          query_complete: false,
          query_status: "sampled",
          query_error_code: "sample_limit",
          lookup_mode: "exact",
          exact_match: true,
        },
        { exact: true },
      ),
    ).toBe("complete");
  });

  it("keeps degraded retained matches scoped to the selected project and source", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: [{ key: "final_status", type: "string", count: 1 }],
        query_complete: false,
        query_status: "degraded",
      },
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "final_status",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.get).toHaveBeenCalledWith(
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        params: {
          project_id: "project-synthetic",
          page_size: 10,
          q: "final_status",
        },
      }),
    );
    expect(result.current.data).toEqual([
      expect.objectContaining({
        id: "final_status",
        category: "attribute",
        type: "string",
        apiColType: "SPAN_ATTRIBUTE",
      }),
    ]);
    expect(result.current.queryReadState).toBe("degraded");
  });

  it("retries a degraded initial retained read without stranding the picker", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: [],
          query_complete: false,
          query_status: "degraded",
          query_error_code: "read_budget_exceeded",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [{ key: "recovered_attribute", type: "string", count: 1 }],
          query_complete: true,
          query_status: "complete",
          browse_mode: "recent_suggestions",
          browse_status: "exhausted",
          has_more: false,
          next_cursor: null,
        },
      });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.queryReadState).toBe("degraded"));
    await act(async () => result.current.refetch());
    await waitFor(() => expect(result.current.queryReadState).toBe("complete"));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.data[0]).toEqual(
      expect.objectContaining({ id: "recovered_attribute" }),
    );
  });

  it("stops unrelated catalog continuation once supplemental exact search succeeds", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.q) {
        if (params.cursor === "catalog-page-2") {
          return Promise.resolve({
            data: {
              result: [{ key: "final_archive", type: "string", count: 1 }],
              query_complete: true,
              query_status: "complete",
              browse_mode: "recent_suggestions",
              browse_status: "exhausted",
              has_more: false,
              next_cursor: null,
            },
          });
        }
        return Promise.resolve({
          data: {
            result: [{ key: "final_category", type: "string", count: 1 }],
            query_complete: true,
            query_status: "complete",
            browse_mode: "recent_suggestions",
            browse_status: "continuation",
            has_more: true,
            next_cursor: "catalog-page-2",
          },
        });
      }
      if (!params.cursor) {
        return Promise.resolve({
          data: {
            result: [],
            query_complete: true,
            query_status: "complete",
            browse_mode: "recent_suggestions",
            browse_status: "continuation",
            has_more: true,
            next_cursor: "search-page-2",
            lookup_mode: "exact",
            exact_match: false,
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "final_status", type: "string", count: 1 }],
          query_complete: true,
          query_status: "complete",
          browse_mode: "recent_suggestions",
          browse_status: "exhausted",
          has_more: false,
          next_cursor: null,
          lookup_mode: "exact",
          exact_match: true,
        },
      });
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "final_status",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.map((item) => item.id)).toEqual([
      "final_status",
      "final_category",
    ]);
    expect(result.current.hasNextPage).toBe(false);
    const completedRequestCount = mocks.get.mock.calls.length;
    await act(async () => result.current.fetchNextPage());
    expect(mocks.get).toHaveBeenCalledTimes(completedRequestCount);

    expect(
      mocks.get.mock.calls.some(
        ([, options]) => options.params.cursor === "catalog-page-2",
      ),
    ).toBe(false);
    expect(mocks.get).toHaveBeenCalledWith(
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        params: {
          project_id: "project-synthetic",
          page_size: 10,
        },
      }),
    );
    expect(mocks.get).toHaveBeenCalledWith(
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        params: {
          project_id: "project-synthetic",
          page_size: 10,
          q: "final_status",
        },
      }),
    );
    expect(mocks.get).toHaveBeenCalledWith(
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        params: {
          project_id: "project-synthetic",
          page_size: 10,
          q: "final_status",
          cursor: "search-page-2",
        },
      }),
    );
    expect(result.current.data.map((item) => item.id)).toEqual([
      "final_status",
      "final_category",
    ]);
    expect(result.current.data[0]).toEqual(
      expect.objectContaining({ id: "final_status", type: "string" }),
    );
    expect(result.current.exactSearchMatched).toBe(true);
    expect(result.current.hasNextPage).toBe(false);
  });

  it("does not certify a punctuation-normalized but distinct raw key", async () => {
    mocks.get.mockImplementation((_url, { params }) =>
      Promise.resolve({
        data: params.q
          ? {
              result: [{ key: "trace_id", type: "string", count: 1 }],
              lookup_mode: "exact",
              exact_match: false,
              browse_status: "exhausted",
              has_more: false,
              next_cursor: null,
            }
          : {
              result: [{ key: "trace_id", type: "string", count: 1 }],
              browse_mode: "retained_catalog",
              browse_status: "continuation",
              has_more: true,
              next_cursor: "catalog-page-2",
            },
      }),
    );

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "trace.id",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.map(({ id }) => id)).toEqual(["trace_id"]);
    expect(result.current.exactSearchMatched).toBe(false);
    expect(result.current.hasNextPage).toBe(true);
  });

  it("prefers authoritative exact type metadata over the retained duplicate", async () => {
    mocks.get.mockImplementation((_url, { params }) =>
      Promise.resolve({
        data: params.q
          ? {
              result: [
                {
                  key: "customer_context",
                  type: "map",
                  types: ["map"],
                  types_exact: true,
                },
              ],
              lookup_mode: "exact",
              exact_match: true,
              browse_status: "exhausted",
              has_more: false,
              next_cursor: null,
            }
          : {
              result: [{ key: "customer_context", type: "string", count: 1 }],
              browse_mode: "retained_catalog",
              browse_status: "continuation",
              has_more: true,
              next_cursor: "catalog-page-2",
            },
      }),
    );

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "customer_context",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([
      expect.objectContaining({
        id: "customer_context",
        type: "map",
        attributeTypes: ["map"],
        attributeTypesExact: true,
      }),
    ]);
    expect(result.current.hasNextPage).toBe(false);
  });

  it("does not query without a project or for an unsupported source", () => {
    const { rerender } = renderHook(
      (props) => useExactTraceAttributeProperties(props),
      {
        initialProps: {
          projectId: "",
          search: "final_status",
          source: "traces",
        },
        wrapper: createWrapper(),
      },
    );

    expect(mocks.get).not.toHaveBeenCalled();
    rerender({
      projectId: "project-synthetic",
      search: "final_status",
      source: "sessions",
    });
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it.each([
    ["retry_count", "number"],
    ["was_escalated", "boolean"],
    ["json_choices", "array"],
    ["customer_context", "map"],
  ])("preserves the exact %s attribute type", async (key, type) => {
    mocks.get.mockResolvedValue({
      data: {
        result: [{ key, type, count: 1 }],
        query_complete: true,
        query_status: "complete",
      },
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: key,
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([
      expect.objectContaining({
        id: key,
        type,
        apiColType: "SPAN_ATTRIBUTE",
      }),
    ]);
  });

  it("preserves every observed storage type for a mixed attribute", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: [
          {
            key: "mixed_status",
            type: "string",
            types: ["string", "number", "boolean"],
            count: 3,
            count_exact: false,
          },
        ],
        query_complete: true,
        query_status: "complete",
        lookup_mode: "exact",
        exact_match: true,
      },
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "mixed_status",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data[0].attributeTypes).toEqual([
      "string",
      "number",
      "boolean",
    ]);
    expect(result.current.data[0].attributeTypesExact).toBe(false);
  });

  it("only certifies storage-type coverage when the server does", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: [
          {
            key: "certified_status",
            type: "string",
            types: ["string"],
            types_exact: true,
          },
        ],
        query_complete: true,
        query_status: "complete",
        lookup_mode: "exact",
        exact_match: true,
      },
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-synthetic",
          search: "certified_status",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data[0].attributeTypesExact).toBe(true);
  });
});
