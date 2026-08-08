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
  default: ({ keys }) => (
    <div data-testid="attribute-keys">
      {keys.map(({ key }) => key).join(",")}
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
});
