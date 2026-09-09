import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS } from "src/sections/projects/LLMTracing/attributeKeyCursorPagination";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    project: {
      spanAttributeKeys: () => "/api/traces/span-attribute-keys/",
    },
  },
}));

import { useLegacyRunInsightAttributeKeys as useRunInsightAttributeKeys } from "../useRunInsightAttributeKeys";
import { generateTraceFilterDefinition } from "../common";

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

describe("useRunInsightAttributeKeys", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads and de-duplicates signed cursor pages", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: [
            { key: "final_status", type: "string" },
            { key: "call.status", type: "string" },
          ],
          has_more: true,
          next_cursor: "attribute-page-2",
          browse_status: "continuation",
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: [
            { key: "final_status", type: "string" },
            { key: "cost_cents", type: "number" },
          ],
          has_more: false,
          next_cursor: null,
          browse_status: "exhausted",
        },
      });

    const { result, rerender } = renderHook(
      () => useRunInsightAttributeKeys("project-large"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    expect(mocks.get).toHaveBeenNthCalledWith(
      1,
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
        params: { project_id: "project-large", page_size: 50 },
      }),
    );

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      "/api/traces/span-attribute-keys/",
      expect.objectContaining({
        timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
        params: {
          project_id: "project-large",
          page_size: 50,
          cursor: "attribute-page-2",
        },
      }),
    );
    expect(result.current.attributeKeys.map(({ key }) => key)).toEqual([
      "final_status",
      "call.status",
      "cost_cents",
    ]);
    const loadedAttributes = result.current.attributeKeys;
    rerender();
    expect(result.current.attributeKeys).toBe(loadedAttributes);
  });

  it("publishes a bounded empty checkpoint for the next explicit request", async () => {
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
          result: [{ key: "older.attribute", type: "string" }],
          has_more: false,
          next_cursor: null,
          browse_status: "exhausted",
        },
      });

    const { result } = renderHook(
      () => useRunInsightAttributeKeys("project-large"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    expect(result.current.attributeKeys).toEqual([]);
    expect(mocks.get).toHaveBeenCalledTimes(1);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));
    expect(result.current.attributeKeys.map(({ key }) => key)).toEqual([
      "older.attribute",
    ]);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.hasNextPage).toBe(false);
  });

  it("terminalizes a repeated cursor instead of offering another page", async () => {
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
          result: [{ key: "refreshed.attribute", type: "number" }],
          has_more: false,
          next_cursor: null,
          browse_status: "exhausted",
        },
      });

    const { result } = renderHook(
      () => useRunInsightAttributeKeys("project-large"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.attributeKeys.map(({ key }) => key)).toEqual([
      "recent.attribute",
    ]);
    await act(async () => result.current.fetchNextPage());
    expect(mocks.get).toHaveBeenCalledTimes(2);

    await act(async () => result.current.retryCursorChain());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    expect(result.current.attributeKeys.map(({ key }) => key)).toEqual([
      "recent.attribute",
      "refreshed.attribute",
    ]);
    expect(mocks.get.mock.calls[2][1].params).not.toHaveProperty("cursor");
  });

  it("offers a sanitized one-request fresh retry after an initial error", async () => {
    mocks.get
      .mockRejectedValueOnce(new Error("sensitive backend detail"))
      .mockResolvedValueOnce({
        data: {
          result: [{ key: "recovered.attribute", type: "string" }],
          has_more: false,
          next_cursor: null,
          browse_status: "exhausted",
        },
      });

    const { result } = renderHook(
      () => useRunInsightAttributeKeys("project-retry"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    await act(async () => result.current.retryCursorChain());
    await waitFor(() =>
      expect(result.current.attributeKeys.map(({ key }) => key)).toEqual([
        "recovered.attribute",
      ]),
    );
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get.mock.calls[1][1].params).not.toHaveProperty("cursor");
  });

  it("retains a selected attribute and its editor type when a new page arrives", () => {
    const selectedFilter = {
      id: "selected-filter",
      column_id: "final_status",
      filter_config: {
        filter_type: "number",
        filter_op: "equals",
        filter_value: [2, ""],
      },
      _meta: { parentProperty: "Attribute" },
    };
    const refreshedDefinition = generateTraceFilterDefinition(
      [],
      [
        { key: "final_status", type: "string" },
        { key: "older.attribute", type: "boolean" },
      ],
      [selectedFilter],
    );
    const attributes = refreshedDefinition.find(
      ({ propertyName }) => propertyName === "Attribute",
    );

    expect(attributes.dependents.map(({ propertyId }) => propertyId)).toEqual([
      "final_status",
      "older.attribute",
    ]);
    expect(
      attributes.dependents.find(
        ({ propertyId }) => propertyId === "final_status",
      ).filterType.type,
    ).toBe("number");
    expect(selectedFilter).toEqual(
      expect.objectContaining({
        column_id: "final_status",
        filter_config: expect.objectContaining({
          filter_type: "number",
          filter_value: [2, ""],
        }),
      }),
    );
  });
});
