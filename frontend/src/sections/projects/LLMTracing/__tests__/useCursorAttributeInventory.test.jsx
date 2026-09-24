import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  propertyCatalog: vi.fn(),
  catalogResult: null,
}));

vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
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
  default: { get: mocks.get },
  endpoints: {
    project: {
      spanAttributeKeys: () => "/api/traces/span-attribute-keys/",
    },
  },
}));

import {
  attributeInventoryKey,
  expandCursorAttributeInventory,
  mergeCursorAttributeRows,
  useCursorAttributeInventory as useUnifiedCursorAttributeInventory,
  useLegacyCursorAttributeInventory as useCursorAttributeInventory,
} from "../useCursorAttributeInventory";

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

const attributePage = (keys, overrides = {}) => ({
  data: {
    result: keys.map((key) => ({ key, type: "string", types: ["string"] })),
    query_complete: true,
    query_status: "complete",
    browse_mode: "recent_suggestions",
    browse_status: "exhausted",
    has_more: false,
    next_cursor: null,
    ...overrides,
  },
});

describe("expandCursorAttributeInventory", () => {
  it("unions typed lanes when the same key arrives from multiple cursor pages", () => {
    expect(
      mergeCursorAttributeRows([
        {
          key: "migrated.attribute",
          type: "string",
          types: ["string"],
          types_exact: true,
        },
        {
          key: "migrated.attribute",
          type: "number",
          types: ["number", "boolean"],
          types_exact: true,
        },
      ]),
    ).toEqual([
      expect.objectContaining({
        key: "migrated.attribute",
        type: "string",
        types: ["string", "number", "boolean"],
        types_exact: false,
      }),
    ]);
  });

  it("keeps representative index-zero paths, enriched types, and saved paths", () => {
    const traceRows = expandCursorAttributeInventory({
      rowType: "traces",
      rawAttributes: [
        {
          key: "customer.tier",
          type: "string",
          types: ["string"],
          types_exact: true,
        },
      ],
      preservedKeys: ["spans.777.saved.only"],
    });

    expect(traceRows).toContain("input");
    expect(traceRows).toContain("spans.0.name");
    expect(
      traceRows.find(
        (row) => attributeInventoryKey(row) === "spans.0.customer.tier",
      ),
    ).toEqual(
      expect.objectContaining({
        type: "string",
        types_exact: true,
      }),
    );
    expect(traceRows.map(attributeInventoryKey)).toEqual(
      expect.arrayContaining(["spans.0.customer.tier", "spans.777.saved.only"]),
    );
    expect(traceRows.map(attributeInventoryKey)).not.toContain(
      "spans.1.customer.tier",
    );

    const sessionRows = expandCursorAttributeInventory({
      rowType: "sessions",
      rawAttributes: [{ key: "attempt", type: "number" }],
    });
    expect(sessionRows).toContain("bookmarked");
    expect(sessionRows).toContain("traces.0.input");
    expect(sessionRows.map(attributeInventoryKey)).toEqual(
      expect.arrayContaining(["traces.0.spans.0.attempt"]),
    );
    expect(sessionRows.map(attributeInventoryKey)).not.toContain(
      "traces.1.spans.2.attempt",
    );
  });

  it("synthesizes only an explicitly typed nonzero path prefix", () => {
    const traceKeys = expandCursorAttributeInventory({
      rowType: "traces",
      rawAttributes: [{ key: "foo", type: "string" }],
      search: "spans.777.foo",
    }).map(attributeInventoryKey);
    expect(traceKeys).toEqual(
      expect.arrayContaining(["spans.0.foo", "spans.777.foo"]),
    );
    expect(traceKeys).not.toContain("spans.776.foo");
    const veryLargeTraceIndexKeys = expandCursorAttributeInventory({
      rowType: "traces",
      rawAttributes: [{ key: "foo", type: "string" }],
      search: "spans.999999999.foo",
    }).map(attributeInventoryKey);
    expect(veryLargeTraceIndexKeys).toHaveLength(traceKeys.length);
    expect(veryLargeTraceIndexKeys).toContain("spans.999999999.foo");

    const sessionKeys = expandCursorAttributeInventory({
      rowType: "sessions",
      rawAttributes: [{ key: "foo", type: "string" }],
      search: "traces.888.spans.999.foo",
    }).map(attributeInventoryKey);
    expect(sessionKeys).toEqual(
      expect.arrayContaining([
        "traces.0.spans.0.foo",
        "traces.888.spans.999.foo",
      ]),
    );
    expect(sessionKeys).not.toContain("traces.888.spans.998.foo");
  });
});

describe("useCursorAttributeInventory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("uses one unified signed catalog chain when the catalog is ready", async () => {
    const fetchNextPage = vi.fn().mockResolvedValue(undefined);
    mocks.catalogResult = {
      data: { pages: [{ metrics: [] }] },
      error: null,
      legacyFallbackRequired: false,
      metrics: [
        {
          name: "customer.tier",
          property_id: "custom_attribute:customer.tier",
          type: "string",
        },
      ],
      hasNextPage: true,
      continuationKey: "catalog-cursor-2",
      fetchNextPage,
      isFetchingNextPage: false,
      isLoading: false,
      isFetching: false,
      isError: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      refetch: vi.fn(),
    };

    const { result } = renderHook(
      () =>
        useUnifiedCursorAttributeInventory({
          projectId: "project-a",
          search: "customer",
        }),
      { wrapper: createWrapper() },
    );

    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.propertyCatalog).toHaveBeenCalledWith(
      expect.objectContaining({
        category: "custom_attribute",
        source: "traces",
        search: "customer",
        projectIds: ["project-a"],
        allowLegacyNotReadyFallback: true,
      }),
    );
    expect(result.current.rawAttributes).toEqual([
      expect.objectContaining({
        key: "customer.tier",
        property_id: "custom_attribute:customer.tier",
      }),
    ]);
    expect(result.current.continuationKey).toBe("catalog-cursor-2");
    expect(result.current.inventoryControlProps.continuationKey).toBe(
      "catalog-cursor-2",
    );
    await act(async () => result.current.fetchNextPage());
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });

  it("uses the authorized workspace cursor scope without a project fan-out", async () => {
    mocks.get.mockResolvedValue(attributePage(["workspace.attribute"]));

    const { result } = renderHook(
      () =>
        useCursorAttributeInventory({
          workspaceScope: true,
          workspaceScopeKey: "workspace-a",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.attributes.map(attributeInventoryKey)).toContain(
        "workspace.attribute",
      ),
    );
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get.mock.calls[0][1].params).toEqual({
      workspace_scope: true,
      page_size: 50,
      discovery_mode: "filter",
    });
  });

  it("waits for the workspace identity before starting its cursor chain", () => {
    renderHook(
      () =>
        useCursorAttributeInventory({
          workspaceScope: true,
          workspaceScopeKey: "",
        }),
      { wrapper: createWrapper() },
    );

    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("uses exact search as a supplement and advances one retained page only on request", async () => {
    mocks.get.mockImplementation((url, { params }) => {
      expect(url).toBe("/api/traces/span-attribute-keys/");
      if (params.q) {
        return Promise.resolve({
          data: {
            result: [
              {
                key: "archive.status",
                type: "string",
                types: ["string"],
              },
            ],
            lookup_mode: "exact",
            exact_match: true,
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      if (params.cursor === "retained-2") {
        return Promise.resolve({
          data: {
            result: [{ key: "older.sibling", type: "number" }],
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "recent.status", type: "string" }],
          has_more: true,
          next_cursor: "retained-2",
          browse_status: "continuation",
        },
      });
    });

    const { result } = renderHook(
      () =>
        useCursorAttributeInventory({
          projectId: "project-large",
          discoveryMode: "filter",
          search: "archive.status",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.exactSearchMatched).toBe(true));
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.attributes.map(attributeInventoryKey)).toEqual(
      expect.arrayContaining(["archive.status", "recent.status"]),
    );
    expect(result.current.hasNextPage).toBe(true);
    expect(result.current.continuationKey).toBe("retained:retained-2");
    expect(result.current.inventoryControlProps.continuationKey).toBe(
      "retained:retained-2",
    );

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(mocks.get).toHaveBeenCalledTimes(3);
    expect(mocks.get.mock.calls[2][1].params).toEqual(
      expect.objectContaining({
        project_id: "project-large",
        discovery_mode: "filter",
        cursor: "retained-2",
      }),
    );
    expect(result.current.attributes.map(attributeInventoryKey)).toContain(
      "older.sibling",
    );
  });

  it("continues a matched workspace exact lane to collect later type families", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.q && params.cursor === "exact-type-2") {
        return Promise.resolve({
          data: {
            result: [
              {
                key: "migrated.attribute",
                type: "number",
                types: ["number"],
              },
            ],
            lookup_mode: "exact",
            exact_match: true,
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      if (params.q) {
        return Promise.resolve({
          data: {
            result: [
              {
                key: "migrated.attribute",
                type: "string",
                types: ["string"],
              },
            ],
            lookup_mode: "exact",
            exact_match: true,
            has_more: true,
            next_cursor: "exact-type-2",
            browse_status: "continuation",
          },
        });
      }
      return Promise.resolve(attributePage(["recent.attribute"]));
    });

    const { result } = renderHook(
      () =>
        useCursorAttributeInventory({
          workspaceScope: true,
          workspaceScopeKey: "workspace-mixed-type",
          search: "migrated.attribute",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.exactSearchMatched).toBe(true));
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(
      result.current.attributes.find(
        (attribute) =>
          attributeInventoryKey(attribute) === "migrated.attribute",
      ),
    ).toEqual(
      expect.objectContaining({
        type: "string",
        types: ["string", "number"],
        types_exact: false,
      }),
    );
    expect(mocks.get.mock.calls.at(-1)[1].params).toEqual(
      expect.objectContaining({
        workspace_scope: true,
        q: "migrated.attribute",
        cursor: "exact-type-2",
      }),
    );
  });

  it("keeps active task schema requests project-wide and preserves a saved mapping", async () => {
    mocks.get.mockImplementation(() => {
      return Promise.resolve({
        data: {
          result: [{ key: "customer.tier", type: "string" }],
          has_more: false,
          next_cursor: null,
          browse_status: "exhausted",
        },
      });
    });

    const { result } = renderHook(
      () =>
        useCursorAttributeInventory({
          projectId: "project-template",
          rowType: "traces",
          discoveryMode: "eval_mapping",
          preservedKeys: ["spans.7.saved.mapping"],
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.attributes.map(attributeInventoryKey)).toContain(
        "spans.0.customer.tier",
      ),
    );
    const keyRequest = mocks.get.mock.calls.find(
      ([url]) => url === "/api/traces/span-attribute-keys/",
    );
    expect(keyRequest[1].params).toEqual({
      project_id: "project-template",
      page_size: 50,
      discovery_mode: "eval_mapping",
    });
    expect(
      mocks.get.mock.calls.every(
        ([, options]) => !("filters" in options.params),
      ),
    ).toBe(true);
    expect(result.current.attributes.map(attributeInventoryKey)).toEqual(
      expect.arrayContaining([
        "spans.0.customer.tier",
        "spans.7.saved.mapping",
      ]),
    );
    expect(result.current.attributes.map(attributeInventoryKey)).not.toContain(
      "spans.3.customer.tier",
    );
  });

  it.each([
    ["traces", "spans.777.foo", "spans.777.foo"],
    ["sessions", "traces.888.spans.999.foo", "traces.888.spans.999.foo"],
  ])(
    "uses raw exact search to synthesize %s paths beyond index zero",
    async (rowType, search, expectedPath) => {
      mocks.get.mockImplementation((_url, { params }) =>
        Promise.resolve(
          params.q
            ? attributePage(["foo"], {
                lookup_mode: "exact",
                exact_match: true,
              })
            : attributePage(["recent"]),
        ),
      );

      const { result } = renderHook(
        () =>
          useCursorAttributeInventory({
            projectId: `project-${rowType}-typed-path`,
            rowType,
            discoveryMode: "eval_mapping",
            search,
          }),
        { wrapper: createWrapper() },
      );

      await waitFor(() =>
        expect(
          result.current.filteredAttributes.map(attributeInventoryKey),
        ).toContain(expectedPath),
      );
      expect(
        mocks.get.mock.calls.some(([, options]) => options.params.q === "foo"),
      ).toBe(true);
    },
  );

  it("keeps voice-call exact search on the raw historical key", async () => {
    mocks.get.mockImplementation((_url, { params }) =>
      Promise.resolve(
        params.q
          ? attributePage(["older.voice.attribute"], {
              lookup_mode: "exact",
              exact_match: true,
            })
          : attributePage(["recent.voice.attribute"]),
      ),
    );

    const { result } = renderHook(
      () =>
        useCursorAttributeInventory({
          projectId: "project-voice-search",
          rowType: "voiceCalls",
          search: "older.voice.attribute",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(
        result.current.filteredAttributes.map(attributeInventoryKey),
      ).toContain("older.voice.attribute"),
    );
    expect(
      mocks.get.mock.calls.some(
        ([, options]) => options.params.q === "older.voice.attribute",
      ),
    ).toBe(true);
  });

  it("demotes a failed exact continuation and advances retained on the next gesture", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.q && params.cursor === "exact-2") {
        return Promise.reject(new Error("exact continuation unavailable"));
      }
      if (params.q) {
        return Promise.resolve(
          attributePage(["foo.sibling"], {
            lookup_mode: "exact",
            exact_match: false,
            browse_status: "continuation",
            has_more: true,
            next_cursor: "exact-2",
          }),
        );
      }
      if (params.cursor === "retained-2") {
        return Promise.resolve(attributePage(["older.foo"]));
      }
      return Promise.resolve(
        attributePage(["recent.foo"], {
          browse_status: "continuation",
          has_more: true,
          next_cursor: "retained-2",
        }),
      );
    });

    const { result } = renderHook(
      () =>
        useCursorAttributeInventory({
          projectId: "project-exact-demotion",
          search: "foo",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.filter(
          ([, options]) => options.params.cursor === "exact-2",
        ),
      ).toHaveLength(1),
    );

    expect(result.current.hasNextPage).toBe(true);
    await act(async () => result.current.fetchNextPage());
    await waitFor(() =>
      expect(result.current.attributes.map(attributeInventoryKey)).toContain(
        "older.foo",
      ),
    );
    expect(
      mocks.get.mock.calls.filter(
        ([, options]) => options.params.cursor === "exact-2",
      ),
    ).toHaveLength(1);
  });

  it("offers one fresh-chain retry for a repeated retained cursor", async () => {
    mocks.get.mockImplementation((_url, { params }) =>
      Promise.resolve(
        params.cursor
          ? attributePage([], {
              browse_status: "continuation",
              has_more: true,
              next_cursor: "same-cursor",
            })
          : attributePage(["recent"], {
              browse_status: "continuation",
              has_more: true,
              next_cursor: "same-cursor",
            }),
      ),
    );

    const { result } = renderHook(
      () => useCursorAttributeInventory({ projectId: "project-stopped" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.isFetchNextPageError).toBe(true));
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    expect(result.current.cursorRetryExhausted).toBe(false);
    expect(result.current.hasNextPage).toBe(true);
    expect(
      mocks.get.mock.calls.filter(([, options]) => options.params.cursor),
    ).toHaveLength(1);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.cursorRetryExhausted).toBe(true));
    expect(result.current.attributes.map(attributeInventoryKey)).toContain(
      "recent",
    );
    expect(result.current.hasNextPage).toBe(false);
    expect(mocks.get).toHaveBeenCalledTimes(4);
  });

  it("preserves loaded rows and permits retry when fresh-chain recovery fails", async () => {
    let freshReads = 0;
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.cursor) {
        return Promise.resolve(
          attributePage([], {
            browse_status: "continuation",
            has_more: true,
            next_cursor: "same-cursor",
          }),
        );
      }
      freshReads += 1;
      if (freshReads === 2) {
        return Promise.reject(new Error("fresh recovery unavailable"));
      }
      return Promise.resolve(
        attributePage(
          [freshReads === 1 ? "recent" : "recovered"],
          freshReads === 1
            ? {
                browse_status: "continuation",
                has_more: true,
                next_cursor: "same-cursor",
              }
            : {},
        ),
      );
    });

    const { result } = renderHook(
      () => useCursorAttributeInventory({ projectId: "project-recovery" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.isFetchNextPageError).toBe(true));

    await act(async () => {
      await expect(result.current.fetchNextPage()).rejects.toThrow(
        "fresh recovery unavailable",
      );
    });

    expect(result.current.cursorRetryExhausted).toBe(false);
    expect(result.current.hasNextPage).toBe(true);
    expect(result.current.attributes.map(attributeInventoryKey)).toContain(
      "recent",
    );

    await act(async () => result.current.fetchNextPage());
    await waitFor(() =>
      expect(result.current.attributes.map(attributeInventoryKey)).toContain(
        "recovered",
      ),
    );
    expect(mocks.get).toHaveBeenCalledTimes(4);
  });

  it("exposes a one-request retry for an initial retained-catalog failure", async () => {
    mocks.get
      .mockRejectedValueOnce(new Error("retained catalog unavailable"))
      .mockResolvedValueOnce(attributePage(["recovered.attribute"]));

    const { result } = renderHook(
      () => useCursorAttributeInventory({ projectId: "project-retry" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.inventoryControlProps.isError).toBe(true),
    );
    expect(result.current.inventoryControlProps.canRetry).toBe(true);

    await act(async () => result.current.inventoryControlProps.onRetry());

    await waitFor(() =>
      expect(result.current.attributes.map(attributeInventoryKey)).toContain(
        "recovered.attribute",
      ),
    );
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.inventoryControlProps.isError).toBe(false);
  });

  it("keeps an initial exact-search failure visible and retryable", async () => {
    let exactAttempts = 0;
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.q) return Promise.resolve(attributePage(["recent"]));
      exactAttempts += 1;
      return exactAttempts === 1
        ? Promise.reject(new Error("exact search unavailable"))
        : Promise.resolve(
            attributePage(["older.exact"], {
              lookup_mode: "exact",
              exact_match: true,
            }),
          );
    });

    const { result } = renderHook(
      () =>
        useCursorAttributeInventory({
          projectId: "project-exact-retry",
          search: "older.exact",
        }),
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(result.current.inventoryControlProps.isExactSearchError).toBe(
        true,
      ),
    );
    await act(async () => result.current.inventoryControlProps.onRetry());

    await waitFor(() => expect(result.current.exactSearchMatched).toBe(true));
    expect(exactAttempts).toBe(2);
    expect(result.current.attributes.map(attributeInventoryKey)).toContain(
      "older.exact",
    );
  });

  it("retries a cached failed exact continuation after raw-search re-entry", async () => {
    let exactFooContinuationAttempts = 0;
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.q) return Promise.resolve(attributePage(["retained"]));
      if (params.q === "other") {
        return Promise.resolve(
          attributePage([], { lookup_mode: "exact", exact_match: false }),
        );
      }
      if (params.cursor === "exact-2") {
        exactFooContinuationAttempts += 1;
        return exactFooContinuationAttempts === 1
          ? Promise.reject(new Error("temporary exact failure"))
          : Promise.resolve(
              attributePage(["foo"], {
                lookup_mode: "exact",
                exact_match: true,
              }),
            );
      }
      return Promise.resolve(
        attributePage(["foo.sibling"], {
          lookup_mode: "exact",
          exact_match: false,
          browse_status: "continuation",
          has_more: true,
          next_cursor: "exact-2",
        }),
      );
    });

    const { result, rerender } = renderHook(
      ({ search }) =>
        useCursorAttributeInventory({
          projectId: "project-search-reentry",
          search,
        }),
      {
        initialProps: { search: "foo" },
        wrapper: createWrapper(),
      },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(exactFooContinuationAttempts).toBe(1));

    rerender({ search: "other" });
    await waitFor(() => expect(result.current.debouncedSearch).toBe("other"));
    rerender({ search: "foo" });

    await waitFor(() => expect(exactFooContinuationAttempts).toBe(2));
    expect(result.current.attributes.map(attributeInventoryKey)).toContain(
      "foo",
    );
  });
});
