import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { useGetScenarioList } from "../scenarios";

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: {
      get: vi.fn(),
    },
  };
});

function createQueryWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
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

describe("useGetScenarioList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.get.mockResolvedValue({
      data: { results: [], next: null, current_page: 1 },
    });
  });

  it("sends selected_scenarios when pinnedScenarioIds is provided", async () => {
    renderHook(
      () =>
        useGetScenarioList("", {
          pinnedScenarioIds: ["scenario-1", "scenario-2"],
        }),
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => expect(axios.get).toHaveBeenCalled());

    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/scenarios/",
      expect.objectContaining({
        params: expect.objectContaining({
          selected_scenarios: JSON.stringify(["scenario-1", "scenario-2"]),
        }),
      }),
    );
  });

  it("omits selected_scenarios when pinnedScenarioIds is empty", async () => {
    renderHook(() => useGetScenarioList("", { pinnedScenarioIds: [] }), {
      wrapper: createQueryWrapper(),
    });

    await waitFor(() => expect(axios.get).toHaveBeenCalled());

    const [, config] = axios.get.mock.calls[0];
    expect(config.params).not.toHaveProperty("selected_scenarios");
  });
});
