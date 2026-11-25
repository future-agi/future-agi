import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS } from "src/sections/projects/LLMTracing/attributeKeyCursorPagination";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  propertyCatalog: vi.fn(),
  catalogResult: null,
}));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    project: { spanAttributeKeys: () => "/span-attribute-keys/" },
  },
}));
vi.mock("react-router-dom", () => ({
  useParams: () => ({ id: "project-large" }),
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
vi.mock("src/components/loading-screen", () => ({
  LoadingScreen: () => <div>Loading attributes…</div>,
}));
vi.mock("../AttributeGroupList", () => ({
  default: ({ groups }) => (
    <div data-testid="attribute-groups">
      {groups.map(({ prefix }) => prefix).join(",")}
    </div>
  ),
}));
vi.mock("../AttributeKeyList", () => ({
  default: ({
    keys,
    hasMore,
    isLoadingMore,
    onLoadMore,
    search,
    onSearchChange,
  }) => (
    <div>
      <input
        aria-label="attribute-search"
        value={search}
        onChange={(event) => onSearchChange(event.target.value)}
      />
      <div data-testid="attribute-keys">
        {keys.map(({ key }) => key).join(",")}
      </div>
      {hasMore && (
        <button disabled={isLoadingMore} onClick={onLoadMore}>
          Load more attributes
        </button>
      )}
    </div>
  ),
}));
vi.mock("../AttributeDetail", () => ({
  default: () => <div data-testid="attribute-detail" />,
}));

import CatalogAttributesView, {
  LegacyAttributesView as AttributesView,
} from "../AttributesView";

function QueryWrapper({ client, children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
QueryWrapper.propTypes = {
  client: PropTypes.instanceOf(QueryClient).isRequired,
  children: PropTypes.node,
};

const renderView = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryWrapper client={client}>
      <AttributesView />
    </QueryWrapper>,
  );
  return client;
};

describe("AttributesView unified catalog", () => {
  it("searches and paginates one signed property-definition chain", async () => {
    const fetchNextPage = vi.fn().mockResolvedValue(undefined);
    mocks.catalogResult = {
      error: null,
      legacyFallbackRequired: false,
      metrics: [
        {
          name: "customer.plan",
          property_id: "custom_attribute:customer.plan",
          type: "string",
        },
      ],
      isLoading: false,
      isFetching: false,
      isError: false,
      cursorChainStopped: false,
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextPage,
      refetch: vi.fn(),
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryWrapper client={client}>
        <CatalogAttributesView />
      </QueryWrapper>,
    );

    expect(await screen.findByTestId("attribute-keys")).toHaveTextContent(
      "customer.plan",
    );
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.propertyCatalog).toHaveBeenCalledWith(
      expect.objectContaining({
        category: "custom_attribute",
        source: "traces",
        projectIds: ["project-large"],
        allowLegacyNotReadyFallback: true,
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });
});

describe("AttributesView errors", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows a sanitized inline retry instead of an empty state on a cold error", async () => {
    mocks.get.mockRejectedValue(new Error("Code 159: secret database host"));

    const client = renderView();

    expect(
      await screen.findByText(
        "Span attributes could not be loaded. Please retry.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("No Span Attributes Found"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/secret|database|Code 159/i),
    ).not.toBeInTheDocument();
    expect(
      client.getQueryCache().find({
        queryKey: ["span-attribute-keys", "project-large", "retained"],
      }).meta,
    ).toMatchObject({ errorHandled: true });

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
  });

  it("keeps loaded attributes visible with a retry alert after a refresh error", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: [
            {
              key: "final_status",
              type: "string",
              count: 42,
              count_exact: true,
            },
          ],
          has_more: false,
          next_cursor: null,
        },
      })
      .mockRejectedValueOnce(new Error("private clickhouse stack trace"));

    const client = renderView();
    expect(await screen.findByTestId("attribute-keys")).toHaveTextContent(
      "final_status",
    );

    await act(async () => {
      await client.refetchQueries({
        queryKey: ["span-attribute-keys", "project-large", "retained"],
      });
    });

    expect(
      await screen.findByText(
        "Span attributes could not be refreshed. Existing attributes are still available.",
      ),
    ).toBeVisible();
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "final_status",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
    expect(
      screen.queryByText(/private|clickhouse|stack trace/i),
    ).not.toBeInTheDocument();
  });

  it("paginates an exact-key search with the signed cursor", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.q) {
        return Promise.resolve({
          data: {
            result: [{ key: "seed.attribute", type: "string" }],
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      if (!params.cursor) {
        return Promise.resolve({
          data: {
            result: [{ key: "final_status_archive", type: "string" }],
            has_more: true,
            next_cursor: "search-page-2",
            browse_status: "continuation",
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "final_status", type: "string" }],
          exact_match: true,
          has_more: true,
          next_cursor: "unneeded-page-3",
          browse_status: "continuation",
        },
      });
    });

    renderView();
    expect(await screen.findByTestId("attribute-keys")).toHaveTextContent(
      "seed.attribute",
    );
    fireEvent.change(screen.getByLabelText("attribute-search"), {
      target: { value: "final_status" },
    });

    expect(await screen.findByTestId("attribute-keys")).toHaveTextContent(
      "final_status_archive",
    );
    expect(mocks.get).toHaveBeenCalledWith(
      "/span-attribute-keys/",
      expect.objectContaining({
        timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
        params: {
          project_id: "project-large",
          page_size: 25,
          q: "final_status",
        },
      }),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.filter(
          ([, options]) => options.params.q === "final_status",
        ),
      ).toHaveLength(2),
    );
    expect(mocks.get).toHaveBeenCalledWith(
      "/span-attribute-keys/",
      expect.objectContaining({
        timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
        params: {
          project_id: "project-large",
          page_size: 25,
          q: "final_status",
          cursor: "search-page-2",
        },
      }),
    );
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "final_status_archive,final_status",
    );
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
  });

  it("keeps exact-match siblings on later retained pages explicitly reachable", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.q) {
        return Promise.resolve({
          data: {
            result: [{ key: "foo", type: "string" }],
            exact_match: true,
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      if (params.cursor === "retained-page-2") {
        return Promise.resolve({
          data: {
            result: [{ key: "foo.bar", type: "number" }],
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "foo_archive", type: "string" }],
          has_more: true,
          next_cursor: "retained-page-2",
          browse_status: "continuation",
        },
      });
    });

    renderView();
    await screen.findByTestId("attribute-keys");
    fireEvent.change(screen.getByLabelText("attribute-search"), {
      target: { value: "foo" },
    });

    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
        "foo,foo_archive",
      ),
    );
    expect(
      mocks.get.mock.calls.some(
        ([, options]) => options.params.cursor === "retained-page-2",
      ),
    ).toBe(false);

    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
        "foo,foo_archive,foo.bar",
      ),
    );
    expect(
      mocks.get.mock.calls.filter(
        ([, options]) => options.params.cursor === "retained-page-2",
      ),
    ).toHaveLength(1);
  });

  it("reveals partial matches on page N one explicit read-more at a time", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.q) {
        return Promise.resolve({
          data: {
            result: [],
            exact_match: false,
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      if (params.cursor === "retained-page-2") {
        return Promise.resolve({
          data: {
            result: Array.from({ length: 25 }, (_, index) => ({
              key: index === 0 ? "foo.v2" : `foo.page2.${index}`,
              type: "string",
            })),
            has_more: true,
            next_cursor: "retained-page-3",
            browse_status: "continuation",
          },
        });
      }
      if (params.cursor === "retained-page-3") {
        return Promise.resolve({
          data: {
            result: [{ key: "foo.bar", type: "number" }],
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "foo_archive", type: "string" }],
          has_more: true,
          next_cursor: "retained-page-2",
          browse_status: "continuation",
        },
      });
    });

    renderView();
    await screen.findByTestId("attribute-keys");
    fireEvent.change(screen.getByLabelText("attribute-search"), {
      target: { value: "foo" },
    });

    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
        "foo_archive",
      ),
    );
    expect(
      mocks.get.mock.calls.some(
        ([, options]) => options.params.cursor === "retained-page-2",
      ),
    ).toBe(false);

    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent("foo.v2"),
    );
    expect(
      mocks.get.mock.calls.some(
        ([, options]) => options.params.cursor === "retained-page-3",
      ),
    ).toBe(false);

    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent("foo.bar"),
    );
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
  });

  it("demotes a failed exact continuation and advances the retained cursor once", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (params.q && params.cursor === "exact-page-2") {
        return Promise.reject(new Error("private exact lookup failure"));
      }
      if (params.q) {
        return Promise.resolve({
          data: {
            result: [{ key: "foo_exact_candidate", type: "string" }],
            exact_match: false,
            has_more: true,
            next_cursor: "exact-page-2",
            browse_status: "continuation",
          },
        });
      }
      if (params.cursor === "retained-page-2") {
        return Promise.resolve({
          data: {
            result: [{ key: "foo.bar", type: "number" }],
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "foo_archive", type: "string" }],
          has_more: true,
          next_cursor: "retained-page-2",
          browse_status: "continuation",
        },
      });
    });

    renderView();
    await screen.findByTestId("attribute-keys");
    fireEvent.change(screen.getByLabelText("attribute-search"), {
      target: { value: "foo" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
        "foo_exact_candidate,foo_archive",
      ),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.filter(
          ([, options]) => options.params.cursor === "exact-page-2",
        ),
      ).toHaveLength(1),
    );
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "foo_exact_candidate,foo_archive",
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
        "foo_exact_candidate,foo_archive,foo.bar",
      ),
    );
    expect(
      mocks.get.mock.calls.filter(
        ([, options]) => options.params.cursor === "exact-page-2",
      ),
    ).toHaveLength(1);
    expect(
      mocks.get.mock.calls.filter(
        ([, options]) => options.params.cursor === "retained-page-2",
      ),
    ).toHaveLength(1);
  });

  it("offers one fresh retry for a repeated cursor, then warns without a loop", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: [{ key: "recent.attribute", type: "string" }],
          has_more: true,
          next_cursor: "same-cursor",
          browse_status: "continuation",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [],
          has_more: true,
          next_cursor: "same-cursor",
          browse_status: "continuation",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [{ key: "recent.attribute", type: "string" }],
          has_more: true,
          next_cursor: "same-cursor",
          browse_status: "continuation",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [],
          has_more: true,
          next_cursor: "same-cursor",
          browse_status: "continuation",
        },
      });

    renderView();
    expect(await screen.findByTestId("attribute-keys")).toHaveTextContent(
      "recent.attribute",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByText(
        "Attribute pagination stopped safely. Existing attributes are still available.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retry pagination" }),
    ).toBeVisible();
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "recent.attribute",
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry pagination" }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    expect(
      await screen.findByRole("button", { name: "Load more attributes" }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(4));
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "recent.attribute",
    );
    expect(
      screen.getByText(
        "Attribute pagination stopped safely. Existing attributes are still available.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Retry pagination" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/private|clickhouse|stack trace|Code 159/i),
    ).not.toBeInTheDocument();
  });

  it("resets an exact cursor retry after another search and re-entry", async () => {
    mocks.get.mockImplementation((_url, { params }) => {
      if (!params.q) {
        return Promise.resolve({
          data: {
            result: [{ key: "foo_archive", type: "string" }],
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      if (params.q === "bar") {
        return Promise.resolve({
          data: {
            result: [{ key: "bar", type: "string" }],
            exact_match: true,
            has_more: false,
            next_cursor: null,
            browse_status: "exhausted",
          },
        });
      }
      if (params.cursor === "same-exact-cursor") {
        return Promise.resolve({
          data: {
            result: [],
            exact_match: false,
            has_more: true,
            next_cursor: "same-exact-cursor",
            browse_status: "continuation",
          },
        });
      }
      return Promise.resolve({
        data: {
          result: [{ key: "foo_candidate", type: "string" }],
          exact_match: false,
          has_more: true,
          next_cursor: "same-exact-cursor",
          browse_status: "continuation",
        },
      });
    });

    renderView();
    await screen.findByTestId("attribute-keys");
    fireEvent.change(screen.getByLabelText("attribute-search"), {
      target: { value: "foo" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
        "foo_candidate,foo_archive",
      ),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(
        mocks.get.mock.calls.filter(
          ([, options]) => options.params.cursor === "same-exact-cursor",
        ),
      ).toHaveLength(1),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Load more attributes" }),
      ).toBeVisible(),
    );
    // The successful fresh page owns a valid advancing cursor, so it remains
    // reachable. Following that cursor repeats the same protocol stop and is
    // terminal for this attempt until a new search gesture resets it.
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Load more attributes" }),
      ).not.toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText("attribute-search"), {
      target: { value: "bar" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("attribute-keys")).toHaveTextContent("bar"),
    );
    fireEvent.change(screen.getByLabelText("attribute-search"), {
      target: { value: "foo" },
    });

    expect(
      await screen.findByRole("button", { name: "Load more attributes" }),
    ).toBeVisible();
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "foo_candidate,foo_archive",
    );
  });

  it("offers one fresh retry for a malformed cursor without losing rows", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: [{ key: "recent.attribute", type: "string" }],
        has_more: true,
        next_cursor: null,
        browse_status: "continuation",
      },
    });

    renderView();
    expect(await screen.findByTestId("attribute-keys")).toHaveTextContent(
      "recent.attribute",
    );
    expect(
      await screen.findByText(
        "Attribute pagination stopped safely. Existing attributes are still available.",
      ),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Retry pagination" }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "recent.attribute",
    );
    expect(
      screen.queryByRole("button", { name: "Retry pagination" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
  });

  it("publishes an empty checkpoint for one explicit continuation gesture", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: [],
          has_more: true,
          next_cursor: "empty-checkpoint",
          browse_status: "continuation",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [{ key: "older.attribute", type: "number" }],
          has_more: false,
          next_cursor: null,
          browse_status: "exhausted",
        },
      });

    renderView();

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Continue loading attributes",
      }),
    );
    expect(await screen.findByTestId("attribute-keys")).toHaveTextContent(
      "older.attribute",
    );
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      "/span-attribute-keys/",
      expect.objectContaining({
        params: {
          project_id: "project-large",
          page_size: 25,
          cursor: "empty-checkpoint",
        },
      }),
    );
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
  });
});
