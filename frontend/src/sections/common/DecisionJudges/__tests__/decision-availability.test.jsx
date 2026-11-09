import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, renderHook } from "@testing-library/react";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { useDecisionModels } from "../useDecisionModels";
import DecisionModelsSection from "../DecisionModelsSection";
import DecisionQuestionEditor from "../DecisionQuestionEditor";

const state = vi.hoisted(() => ({
  keys: vi.fn((_options = { enabled: false }) => ({ data: [] })),
  query: vi.fn(),
  refetch: vi.fn(() => Promise.resolve(null)),
}));
vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: (options) => state.keys(options),
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: (options) => {
    state.query(options);
    return {
      data: {
        object: "list",
        data: [
          {
            id: "typesafe/jev-1.14.0",
            provider: "typesafe",
            format: "systemone",
          },
        ],
      },
      isLoading: true,
      isError: true,
      refetch: state.refetch,
    };
  },
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { decisionModels: "/agentcc/decisions/models/" } },
}));

beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("decision model availability", () => {
  it.each([false, true])(
    "hides cached presets and makes no query or manual retry in real builds (basic=%s)",
    async (experimentsBasic) => {
      setLaunchOverrideForTest({ launch: true, experimentsBasic });
      const { result } = renderHook(() => useDecisionModels());
      expect(state.keys).toHaveBeenLastCalledWith({ enabled: false });
      expect(state.query).toHaveBeenLastCalledWith(
        expect.objectContaining({ enabled: false }),
      );
      expect(result.current.listing).toBeNull();
      expect(result.current.rows).toEqual([]);
      expect(result.current.isLoading).toBe(false);
      expect(result.current.isError).toBe(false);
      await result.current.refetch();
      expect(state.refetch).not.toHaveBeenCalled();
    },
  );

  it.each([false, true])(
    "does not render the model menu or question editor in real builds (basic=%s)",
    (experimentsBasic) => {
      setLaunchOverrideForTest({ launch: true, experimentsBasic });
      const { container } = render(
        <>
          <DecisionModelsSection model="" onPick={vi.fn()} onAddKey={vi.fn()} />
          <DecisionQuestionEditor
            value={{ model: "typesafe/jev-1.14.0" }}
            onChange={vi.fn()}
            outputType="boolean"
          />
        </>,
      );
      expect(container.innerHTML).toBe("");
      expect(
        state.keys.mock.calls.every(([options]) => options.enabled === false),
      ).toBe(true);
      expect(
        state.query.mock.calls.every(([options]) => options.enabled === false),
      ).toBe(true);
    },
  );

  it("keeps listing, presets and retry available in internal preview", async () => {
    setLaunchOverrideForTest({ launch: false });
    const { result } = renderHook(() => useDecisionModels());
    expect(state.keys).toHaveBeenLastCalledWith({ enabled: true });
    expect(state.query).toHaveBeenLastCalledWith(
      expect.objectContaining({ enabled: true }),
    );
    expect(result.current.rows.length).toBeGreaterThan(0);
    expect(result.current.listing.data).toHaveLength(1);
    await result.current.refetch();
    expect(state.refetch).toHaveBeenCalledTimes(1);
    const { container } = render(
      <DecisionModelsSection model="" onPick={vi.fn()} onAddKey={vi.fn()} />,
    );
    expect(
      container.querySelector('[data-testid="decision-models-section"]'),
    ).not.toBeNull();
  });

  it("honors a caller disabling queries and retry in preview", async () => {
    setLaunchOverrideForTest({ launch: false });
    const { result } = renderHook(() => useDecisionModels({ enabled: false }));
    expect(state.keys).toHaveBeenLastCalledWith({ enabled: false });
    expect(state.query).toHaveBeenLastCalledWith(
      expect.objectContaining({ enabled: false }),
    );
    await result.current.refetch();
    expect(state.refetch).not.toHaveBeenCalled();
  });
});
