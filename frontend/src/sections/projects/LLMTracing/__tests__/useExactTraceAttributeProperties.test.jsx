import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS } from "../attributeKeyCursorPagination";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  debouncedValue: undefined,
  propertyCatalog: vi.fn(),
  catalogResult: null,
}));

vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) =>
    mocks.debouncedValue === undefined ? value : mocks.debouncedValue,
}));

vi.mock("src/hooks/useDashboards", () => ({
  isPropertyCatalogNotReadyError: (error) =>
    error?.response?.status === 503 &&
    error?.response?.data?.code === "property_catalog_not_ready",
  usePropertyCatalog: (options) => {
    mocks.propertyCatalog(options);
    return mocks.catalogResult;
  },
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
  useExactTraceAttributeProperties as useUnifiedExactTraceAttributeProperties,
  useLegacyExactTraceAttributeProperties as useExactTraceAttributeProperties,
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
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.debouncedValue = undefined;
    mocks.catalogResult = {
      error: {
        response: {
          status: 503,
          data: { code: "property_catalog_not_ready" },
        },
      },
      legacyFallbackRequired: true,
      metrics: [],
    };
  });

  it("uses the unified catalog as the authoritative searchable definition list", async () => {
    const fetchNextPage = vi.fn().mockResolvedValue(undefined);
    mocks.catalogResult = {
      data: { pages: [{ metrics: [] }] },
      error: null,
      legacyFallbackRequired: false,
      metrics: [
        {
          name: "customer.plan",
          display_name: "Customer plan",
          property_id: "custom_attribute:customer.plan",
          type: "string",
        },
      ],
      hasNextPage: true,
      fetchNextPage,
      isFetchingNextPage: false,
      isLoading: false,
      isFetching: false,
      isError: false,
      isSuccess: true,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      queryReadState: "complete",
      refetch: vi.fn(),
    };

    const { result } = renderHook(
      () =>
        useUnifiedExactTraceAttributeProperties({
          projectId: "project-a",
          search: "customer.plan",
          source: "spans",
        }),
      { wrapper: createWrapper() },
    );

    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.propertyCatalog).toHaveBeenCalledWith(
      expect.objectContaining({
        category: "custom_attribute",
        source: "traces",
        search: "customer.plan",
        projectIds: ["project-a"],
        pageSize: 20,
        allowLegacyNotReadyFallback: true,
      }),
    );
    expect(result.current.data).toEqual([
      expect.objectContaining({
        id: "customer.plan",
        registryId: "custom_attribute:customer.plan",
        name: "Customer plan",
      }),
    ]);
    expect(result.current.exactSearchMatched).toBe(true);
    await act(async () => result.current.fetchNextExactPage());
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });

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
          total_count: 73,
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
          total_count: 73,
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
    expect(result.current.totalCount).toBe(73);
    expect(mocks.get).toHaveBeenNthCalledWith(
      1,
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
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
        timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
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
    expect(result.current.totalCount).toBe(73);
  });

  it("continues a resumable limit_reached catalog into its next batch", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: [{ key: "recent.attribute", type: "string", count: 1 }],
          query_complete: true,
          query_status: "complete",
          browse_mode: "recent_suggestions",
          browse_status: "limit_reached",
          has_more: true,
          next_cursor: "next-bounded-batch",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [{ key: "older.attribute", type: "string", count: 1 }],
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

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    expect(result.current.browseLimitReached).toBe(false);
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("next-bounded-batch");
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "recent.attribute",
      "older.attribute",
    ]);
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
    for (let expectedCalls = 2; expectedCalls <= 14; expectedCalls += 1) {
      await act(async () => result.current.fetchNextPage());
      await waitFor(() =>
        expect(mocks.get).toHaveBeenCalledTimes(expectedCalls),
      );
      expect(result.current.hasNextPage).toBe(true);
    }

    expect(result.current.hasNextPage).toBe(true);
    expect(result.current.isFetchingNextPage).toBe(false);
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "recent.attribute",
    ]);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(15));
    expect(result.current.hasNextPage).toBe(true);
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

  it("retries a repeated retained cursor once, then terminalizes with prior keys intact", async () => {
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
      })
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

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    expect(result.current.cursorRetryExhausted).toBe(false);
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.cursorRetryExhausted).toBe(true));

    expect(mocks.get).toHaveBeenCalledTimes(4);
    expect(result.current.hasNextPage).toBe(false);
    expect(result.current.isFetchNextPageError).toBe(false);
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "recent.attribute",
    ]);

    await act(async () => result.current.fetchNextPage());
    expect(mocks.get).toHaveBeenCalledTimes(4);
  });

  it("keeps an unchanged exhausted catalog after a successful cached refetch", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: [{ key: "final_status", type: "string", count: 1 }],
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

  it("refreshes a long retained chain with one request and preserves older rows", async () => {
    let freshReads = 0;
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.cursor) {
        freshReads += 1;
        return Promise.resolve({
          data: {
            result: [
              {
                key: freshReads === 1 ? "recent.attribute" : "fresh.attribute",
                type: "string",
              },
            ],
            browse_status: "continuation",
            has_more: true,
            next_cursor: freshReads === 1 ? "page-2" : "fresh-page-2",
          },
        });
      }
      const pageNumber = Number(params.cursor.split("-").at(-1));
      return Promise.resolve({
        data: {
          result: [{ key: `older.attribute.${pageNumber}`, type: "string" }],
          browse_status: pageNumber === 5 ? "exhausted" : "continuation",
          has_more: pageNumber < 5,
          next_cursor: pageNumber < 5 ? `page-${pageNumber + 1}` : null,
        },
      });
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-long-chain",
          search: "",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    for (let page = 2; page <= 5; page += 1) {
      await act(async () => result.current.fetchNextPage());
    }
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));
    expect(mocks.get).toHaveBeenCalledTimes(5);

    await act(async () => result.current.refetch());

    expect(mocks.get).toHaveBeenCalledTimes(6);
    expect(mocks.get.mock.calls.at(-1)[1].params).not.toHaveProperty("cursor");
    expect(result.current.data.map(({ id }) => id)).toEqual(
      expect.arrayContaining([
        "recent.attribute",
        "older.attribute.5",
        "fresh.attribute",
      ]),
    );
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

  it.each([
    ["tracing", "traces"],
    ["voice", "spans"],
  ])(
    "keeps the %s retained catalog explicitly pageable after an exact-prefix collision",
    async (surface, source) => {
      mocks.get.mockImplementation((_url, { params }) => {
        if (!params.q) {
          if (params.cursor === "catalog-page-2") {
            return Promise.resolve({
              data: {
                result: [{ key: "foo.bar", type: "string", count: 1 }],
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
              result: [{ key: "foo_archive", type: "string", count: 1 }],
              query_complete: true,
              query_status: "complete",
              browse_mode: "recent_suggestions",
              browse_status: "continuation",
              has_more: true,
              next_cursor: "catalog-page-2",
            },
          });
        }
        return Promise.resolve({
          data: {
            result: [{ key: "foo", type: "string", count: 1 }],
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
            projectId: `project-${surface}`,
            search: "foo",
            source,
          }),
        { wrapper: createWrapper() },
      );

      await waitFor(() => expect(result.current.exactSearchMatched).toBe(true));
      expect(result.current.data.map((item) => item.id)).toEqual([
        "foo",
        "foo_archive",
      ]);
      expect(result.current.hasNextExactPage).toBe(false);
      expect(result.current.hasNextPage).toBe(true);
      expect(
        mocks.get.mock.calls.some(
          ([, options]) => options.params.cursor === "catalog-page-2",
        ),
      ).toBe(false);

      await act(async () => result.current.fetchNextPage());
      await waitFor(() => expect(result.current.hasNextPage).toBe(false));

      expect(
        mocks.get.mock.calls.filter(
          ([, options]) => options.params.cursor === "catalog-page-2",
        ),
      ).toHaveLength(1);
      expect(result.current.data.map((item) => item.id)).toEqual([
        "foo",
        "foo_archive",
        "foo.bar",
      ]);
    },
  );

  it("keeps retained partial matches usable when supplemental exact search fails", async () => {
    const exactFailure = new Error("exact search unavailable");
    let exactShouldFail = true;
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.q) {
        return exactShouldFail
          ? Promise.reject(exactFailure)
          : Promise.resolve({
              data: {
                result: [{ key: "prompt", type: "string", count: 1 }],
                query_complete: true,
                query_status: "complete",
                browse_mode: "recent_suggestions",
                browse_status: "exhausted",
                lookup_mode: "exact",
                exact_match: true,
                has_more: false,
                next_cursor: null,
              },
            });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "prompt_slug_archive", type: "string", count: 1 }],
          query_complete: true,
          query_status: "complete",
          browse_mode: "recent_suggestions",
          browse_status: "exhausted",
          has_more: false,
          next_cursor: null,
        },
      });
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-whatfix",
          search: "prompt",
          source: "spans",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.exactSearchError).toBe(exactFailure),
    );
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "prompt_slug_archive",
    ]);
    expect(result.current.queryReadState).toBe("complete");
    expect(result.current.isError).toBe(false);
    expect(result.current.isSuccess).toBe(true);
    expect(result.current.hasNextExactPage).toBe(true);
    expect(result.current.isFetchNextPageError).toBe(true);

    exactShouldFail = false;
    await act(async () => result.current.fetchNextExactPage());
    await waitFor(() => expect(result.current.exactSearchMatched).toBe(true));

    expect(result.current.exactSearchError).toBeNull();
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "prompt",
      "prompt_slug_archive",
    ]);
  });

  it.each([
    ["tracing", "traces"],
    ["voice", "spans"],
  ])(
    "retries one cached failed %s exact continuation after rapid re-entry",
    async (_surface, source) => {
      mocks.debouncedValue = "foo";
      let continuationAttempts = 0;
      mocks.get.mockImplementation((_url, { params }) => {
        if (!params.q) {
          return Promise.resolve({
            data: {
              result: [{ key: "foo_archive", type: "string" }],
              browse_mode: "recent_suggestions",
              browse_status: "exhausted",
              has_more: false,
              next_cursor: null,
            },
          });
        }
        if (!params.cursor) {
          return Promise.resolve({
            data: {
              result: [{ key: "foo_archive", type: "string" }],
              lookup_mode: "exact",
              exact_match: false,
              browse_status: "continuation",
              has_more: true,
              next_cursor: "foo-page-2",
            },
          });
        }
        continuationAttempts += 1;
        if (continuationAttempts === 1) {
          return Promise.reject(new Error("continuation unavailable"));
        }
        return Promise.resolve({
          data: {
            result: [{ key: "foo", type: "string" }],
            lookup_mode: "exact",
            exact_match: true,
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        });
      });

      const { result, rerender } = renderHook(
        ({ search }) =>
          useExactTraceAttributeProperties({
            projectId: `project-${_surface}`,
            search,
            source,
          }),
        {
          initialProps: { search: "foo" },
          wrapper: createWrapper(),
        },
      );

      await waitFor(() => expect(result.current.hasNextExactPage).toBe(true));
      await act(async () => result.current.fetchNextExactPage());
      await waitFor(() =>
        expect(result.current.isFetchNextPageError).toBe(true),
      );
      expect(continuationAttempts).toBe(1);

      // The settled query remains `foo`; this models clear+retype inside the
      // 350 ms debounce window. Re-entry retries c1 once, not cursorless p1.
      rerender({ search: "" });
      rerender({ search: "foo" });
      await waitFor(() => expect(result.current.exactSearchMatched).toBe(true));

      expect(continuationAttempts).toBe(2);
      expect(
        mocks.get.mock.calls.filter(
          ([, options]) => options.params.q === "foo" && !options.params.cursor,
        ),
      ).toHaveLength(1);
      expect(result.current.data).toEqual(
        expect.arrayContaining([expect.objectContaining({ id: "foo" })]),
      );
    },
  );

  it("gives a re-entered exact search one fresh stopped-cursor retry", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.q) {
        return Promise.resolve({
          data: {
            result: [{ key: "prompt_slug_archive", type: "string" }],
            browse_mode: "recent_suggestions",
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [],
          lookup_mode: "exact",
          exact_match: false,
          browse_status: "continuation",
          has_more: true,
          next_cursor: `${params.q}-same-cursor`,
        },
      });
    });
    const cursorlessFooCalls = () =>
      mocks.get.mock.calls.filter(
        ([, options]) => options.params.q === "foo" && !options.params.cursor,
      ).length;

    const { result, rerender } = renderHook(
      ({ search }) =>
        useExactTraceAttributeProperties({
          projectId: "project-coletia",
          search,
          source: "traces",
        }),
      {
        initialProps: { search: "foo" },
        wrapper: createWrapper(),
      },
    );

    await waitFor(() => expect(result.current.hasNextExactPage).toBe(true));
    expect(cursorlessFooCalls()).toBe(1);
    await act(async () => result.current.fetchNextExactPage());
    await waitFor(() => expect(result.current.isFetchNextPageError).toBe(true));
    expect(result.current.cursorRetryExhausted).toBe(false);
    await act(async () => result.current.fetchNextExactPage());
    await waitFor(() => expect(cursorlessFooCalls()).toBe(2));
    expect(result.current.cursorRetryExhausted).toBe(false);
    await act(async () => result.current.fetchNextExactPage());
    await waitFor(() => expect(result.current.cursorRetryExhausted).toBe(true));
    expect(cursorlessFooCalls()).toBe(2);

    rerender({ search: "bar" });
    await waitFor(() => expect(result.current.debouncedSearch).toBe("bar"));
    rerender({ search: "foo" });
    await waitFor(() => {
      expect(result.current.cursorRetryExhausted).toBe(false);
      expect(result.current.hasNextExactPage).toBe(true);
    });

    await act(async () => result.current.fetchNextExactPage());
    await waitFor(() => expect(cursorlessFooCalls()).toBe(3));
    expect(result.current.cursorRetryExhausted).toBe(false);
    await act(async () => result.current.fetchNextExactPage());
    await waitFor(() => expect(result.current.cursorRetryExhausted).toBe(true));
  });

  it("advances only the exact prompt_slug cursor after one sparse bounded search", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.q) {
        return Promise.resolve({
          data: {
            result: [{ key: "recent_attribute", type: "string" }],
            browse_status: "continuation",
            has_more: true,
            next_cursor: "catalog-page-2",
          },
        });
      }
      if (params.cursor === "exact-13") {
        return Promise.resolve({
          data: {
            result: [{ key: "prompt_slug", type: "string" }],
            lookup_mode: "exact",
            exact_match: true,
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        });
      }
      const index = params.cursor
        ? Number(params.cursor.slice("exact-".length))
        : 0;
      return Promise.resolve({
        data: {
          result: [],
          lookup_mode: "exact",
          exact_match: false,
          browse_status: "continuation",
          has_more: true,
          next_cursor: `exact-${index + 1}`,
        },
      });
    });

    const { result } = renderHook(
      () =>
        useExactTraceAttributeProperties({
          projectId: "project-coletia",
          search: "prompt_slug",
          source: "traces",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextExactPage).toBe(true);
    expect(result.current.exactSearchMatched).toBe(false);
    // The shared Load-more method is what Basic/Query picker gestures call.
    // With an active search it must advance exactly one logical cursor chain,
    // never the unrelated retained catalog.
    for (
      let expectedExactPage = 1;
      expectedExactPage <= 13;
      expectedExactPage += 1
    ) {
      await act(async () => result.current.fetchNextPage());
      await waitFor(() =>
        expect(
          mocks.get.mock.calls.some(
            ([, options]) =>
              options.params.cursor === `exact-${expectedExactPage}`,
          ),
        ).toBe(true),
      );
      expect(
        mocks.get.mock.calls.some(
          ([, options]) => options.params.cursor === "catalog-page-2",
        ),
      ).toBe(false);
    }
    await waitFor(() => expect(result.current.exactSearchMatched).toBe(true));

    expect(
      mocks.get.mock.calls.some(
        ([, options]) => options.params.cursor === "catalog-page-2",
      ),
    ).toBe(false);
    expect(mocks.get).toHaveBeenCalledWith(
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        params: {
          project_id: "project-coletia",
          page_size: 10,
          q: "prompt_slug",
          cursor: "exact-13",
        },
      }),
    );
    expect(result.current.data[0]).toEqual(
      expect.objectContaining({
        id: "prompt_slug",
        type: "string",
        attributeTypesExact: false,
      }),
    );
    // Exact discovery is complete, but the retained catalog still advertises
    // an independent explicit continuation.
    expect(result.current.hasNextPage).toBe(true);
  });

  it("resumes only the retained cursor after an absent exact search is exhausted", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.q) {
        return Promise.resolve({
          data: {
            result: [{ key: "trace_id", type: "string", count: 1 }],
            lookup_mode: "exact",
            exact_match: false,
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        });
      }
      if (params.cursor === "catalog-page-2") {
        return Promise.resolve({
          data: {
            result: [{ key: "trace.id.archive", type: "string", count: 1 }],
            browse_mode: "recent_suggestions",
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "trace_id", type: "string", count: 1 }],
          browse_mode: "recent_suggestions",
          browse_status: "continuation",
          has_more: true,
          next_cursor: "catalog-page-2",
        },
      });
    });

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
    expect(result.current.hasNextExactPage).toBe(false);
    expect(result.current.hasNextPage).toBe(true);
    const completedRequestCount = mocks.get.mock.calls.length;
    await act(async () => result.current.fetchNextPage());
    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledTimes(completedRequestCount + 1),
    );
    expect(mocks.get).toHaveBeenLastCalledWith(
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        params: {
          project_id: "project-synthetic",
          page_size: 10,
          cursor: "catalog-page-2",
        },
      }),
    );
    expect(result.current.data.map(({ id }) => id)).toEqual([
      "trace_id",
      "trace.id.archive",
    ]);
    expect(result.current.hasNextPage).toBe(false);
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
              browse_mode: "recent_suggestions",
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
    expect(result.current.hasNextPage).toBe(true);
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
