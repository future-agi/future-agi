import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  useGetLibraryTemplatesInfinite,
  useGetPromptTemplatesInfinite,
} from "../prompts";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    develop: {
      runPrompt: {
        createTemplateId: "/model-hub/prompt-templates/",
        promptTemplate: "/model-hub/prompt-base-templates/",
      },
    },
  },
}));

function createQueryWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  function QueryWrapper({ children }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  }

  QueryWrapper.propTypes = {
    children: PropTypes.node,
  };

  return QueryWrapper;
}

function templates(count, prefix = "template") {
  return Array.from({ length: count }, (_, index) => ({
    id: `${prefix}-${index}`,
    name: `${prefix} ${index}`,
  }));
}

describe("useGetLibraryTemplatesInfinite", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("requests library templates with search and generated plus legacy pagination aliases", async () => {
    axios.get
      .mockResolvedValueOnce({
        data: { result: { data: templates(10), total_count: 12 } },
      })
      .mockResolvedValueOnce({
        data: { result: { data: templates(2, "next"), total_count: 12 } },
      });

    const { result } = renderHook(
      () => useGetLibraryTemplatesInfinite("research"),
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(axios.get).toHaveBeenCalledWith(
      "/model-hub/prompt-base-templates/",
      expect.objectContaining({
        params: {
          name: "research",
          page: 1,
          limit: 10,
          page_size: 10,
          page_number: 0,
        },
        signal: expect.any(AbortSignal),
      }),
    );

    result.current.fetchNextPage();

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));

    expect(axios.get).toHaveBeenLastCalledWith(
      "/model-hub/prompt-base-templates/",
      expect.objectContaining({
        params: {
          name: "research",
          page: 2,
          limit: 10,
          page_size: 10,
          page_number: 1,
        },
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("reads legacy library template result envelopes", async () => {
    axios.get
      .mockResolvedValueOnce({
        data: { result: { data: templates(10), total_count: 11 } },
      })
      .mockResolvedValueOnce({
        data: {
          result: { data: templates(1, "legacy-next"), total_count: 11 },
        },
      });

    const { result } = renderHook(
      () => useGetLibraryTemplatesInfinite("legacy"),
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    result.current.fetchNextPage();

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));

    expect(axios.get).toHaveBeenLastCalledWith(
      "/model-hub/prompt-base-templates/",
      expect.objectContaining({
        params: {
          name: "legacy",
          page: 2,
          limit: 10,
          page_size: 10,
          page_number: 1,
        },
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("continues to read wrapped generated-style library template result envelopes", async () => {
    axios.get
      .mockResolvedValueOnce({
        data: {
          result: {
            count: 11,
            next: 2,
            previous: null,
            results: templates(10),
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            count: 11,
            next: null,
            previous: 1,
            results: templates(1, "wrapped-next"),
          },
        },
      });

    const { result } = renderHook(
      () => useGetLibraryTemplatesInfinite("wrapped"),
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    result.current.fetchNextPage();

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));

    expect(axios.get).toHaveBeenLastCalledWith(
      "/model-hub/prompt-base-templates/",
      expect.objectContaining({
        params: {
          name: "wrapped",
          page: 2,
          limit: 10,
          page_size: 10,
          page_number: 1,
        },
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("keeps library templates on a separate endpoint and pagination contract from saved prompts", async () => {
    axios.get.mockResolvedValueOnce({
      data: { results: [], current_page: 1, total_pages: 1 },
    });

    const { result } = renderHook(
      () => useGetPromptTemplatesInfinite("research"),
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(axios.get).toHaveBeenCalledWith(
      "/model-hub/prompt-templates/",
      expect.objectContaining({
        params: {
          modality: "chat",
          name: "research",
          page_size: 10,
          page: 1,
        },
        signal: expect.any(AbortSignal),
      }),
    );
  });
});
