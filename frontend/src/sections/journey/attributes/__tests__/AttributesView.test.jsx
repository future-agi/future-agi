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

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

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

import AttributesView from "../AttributesView";

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
        queryKey: ["span-attribute-keys", "project-large", ""],
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
        queryKey: ["span-attribute-keys", "project-large", ""],
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
            result: [{ key: "final_state", type: "string" }],
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
      "final_state",
    );
    expect(mocks.get).toHaveBeenCalledWith(
      "/span-attribute-keys/",
      expect.objectContaining({
        timeout: 35_000,
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
        timeout: 35_000,
        params: {
          project_id: "project-large",
          page_size: 25,
          q: "final_status",
          cursor: "search-page-2",
        },
      }),
    );
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "final_state,final_status",
    );
    expect(
      screen.queryByRole("button", { name: "Load more attributes" }),
    ).not.toBeInTheDocument();
  });

  it("terminalizes a repeated cursor without leaving Load more available", async () => {
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
      });

    renderView();
    expect(await screen.findByTestId("attribute-keys")).toHaveTextContent(
      "recent.attribute",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Load more attributes" }),
    );

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Load more attributes" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("attribute-keys")).toHaveTextContent(
      "recent.attribute",
    );
  });

  it("follows an empty transport checkpoint before publishing the page", async () => {
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
