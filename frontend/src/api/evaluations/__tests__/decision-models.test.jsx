import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import useDecisionModels, { decisionModelsQueryKey } from "../decision-models";
import { ModelHubApiModelsListListResponse } from "src/generated/api-contracts/api.zod";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: vi.fn() },
}));

const model = {
  model_name: "morph/reflex",
  label: "Reflex",
  author: "Morph",
  version: "versioned",
  question_types: ["noul", "choice", "score"],
  min_choices: 1,
  max_choices: 255,
  max_state_bytes: 120000,
  notice: "Text and JSON inputs only.",
  unavailable_reason: null,
  type: "decision",
  connections: [
    {
      model_name: "decision/openrouter/morph/reflex",
      provider: "openrouter",
      label: "OpenRouter",
      is_available: false,
    },
  ],
};

beforeEach(() => vi.clearAllMocks());

function queryWrapper(client) {
  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

describe("decision catalog query", () => {
  it("keeps ordinary model metadata compatible in the shared generated contract", () => {
    const row = {
      model_name: "speech-model",
      type: "tts",
      best_for: ["real-time speech"],
      use_case: "voice assistant",
      cutoff: "2026-10",
      latency: 100,
      pricing: { input_per_minute: 0.0077 },
    };
    expect(
      ModelHubApiModelsListListResponse.parse({ count: 1, results: [row] })
        .results,
    ).toEqual([row]);
  });

  it.each([
    { ...model, max_state_bytes: "unknown" },
    {
      ...model,
      connections: [{ ...model.connections[0], is_available: "yes" }],
    },
    { ...model, question_types: undefined },
    { ...model, type: "chat" },
  ])(
    "rejects malformed catalog rows rather than caching partial choices",
    async (row) => {
      axios.get.mockResolvedValue({ data: { count: 1, results: [row] } });
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const { result, unmount } = renderHook(() => useDecisionModels(), {
        wrapper: queryWrapper(client),
      });
      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(result.current.data).toBeUndefined();
      expect(client.getQueryData(decisionModelsQueryKey)).toBeUndefined();
      unmount();
      client.clear();
    },
  );

  it("waits until enabled and refreshes readiness through the shared invalidation key", async () => {
    let configured = false;
    axios.get.mockImplementation(async () => ({
      data: {
        count: 1,
        next: null,
        previous: null,
        current_page: 1,
        total_pages: 1,
        results: [
          {
            ...model,
            connections: model.connections.map((connection) => ({
              ...connection,
              is_available: configured,
            })),
          },
        ],
      },
    }));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, rerender, unmount } = renderHook(
      ({ enabled }) => useDecisionModels(enabled),
      { initialProps: { enabled: false }, wrapper: queryWrapper(client) },
    );
    expect(axios.get).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(endpoints.develop.modelList, {
      params: { model_type: "decision", limit: 100 },
    });
    expect(result.current.data[0].connections[0].is_available).toBe(false);
    expect(client.getQueryData(decisionModelsQueryKey)).toEqual(
      result.current.data,
    );

    configured = true;
    await act(async () =>
      client.invalidateQueries({ queryKey: decisionModelsQueryKey }),
    );
    await waitFor(() =>
      expect(result.current.data[0].connections[0].is_available).toBe(true),
    );
    expect(result.current.data[0].connections[0].model_name).toBe(
      model.connections[0].model_name,
    );
    unmount();
    client.clear();
  });
});
