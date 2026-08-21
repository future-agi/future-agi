import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: { develop: { eval: { aiFilter: "/ai-filter/" } } },
}));

import {
  SMART_AI_FILTER_TIMEOUT_MS,
  useAIFilter,
} from "src/hooks/use-ai-filter";

const schema = [
  {
    field: "model",
    label: "Model",
    type: "string",
    operators: ["is", "contains"],
  },
];

describe("useAIFilter smart grounding contract", () => {
  beforeEach(() => {
    mocks.post.mockReset();
  });

  it("uses the bounded smart endpoint without a legacy retry", async () => {
    const filters = [{ field: "model", operator: "is", value: "gpt-4o" }];
    mocks.post.mockResolvedValue({ data: { result: { filters } } });
    const { result } = renderHook(() => useAIFilter(schema));

    let parsed;
    await act(async () => {
      parsed = await result.current.parseQuery("model gpt-4o", {
        smart: true,
        projectId: "project-1",
        source: "traces",
      });
    });

    expect(parsed).toEqual(filters);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenCalledWith(
      "/ai-filter/",
      {
        query: "model gpt-4o",
        schema,
        mode: "smart",
        project_id: "project-1",
        source: "traces",
      },
      { timeout: SMART_AI_FILTER_TIMEOUT_MS },
    );
  });

  it("surfaces a typed smart refusal instead of returning fallback filters", async () => {
    mocks.post.mockRejectedValue({
      response: {
        status: 422,
        data: { result: "AI value grounding needs a more specific value." },
      },
    });
    const { result } = renderHook(() => useAIFilter(schema));

    let refusal;
    await act(async () => {
      try {
        await result.current.parseQuery("model gpt", {
          smart: true,
          projectId: "project-1",
          source: "traces",
        });
      } catch (error) {
        refusal = error;
      }
    });

    expect(refusal).toBeDefined();
    expect(mocks.post).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(result.current.error).toBe(
        "AI value grounding needs a more specific value.",
      ),
    );
  });

  it("refuses smart mode without project scope before making a request", async () => {
    const { result } = renderHook(() => useAIFilter(schema));

    let refusal;
    await act(async () => {
      try {
        await result.current.parseQuery("model gpt-4o", { smart: true });
      } catch (error) {
        refusal = error;
      }
    });

    expect(refusal?.message).toBe(
      "Select a project before using AI value grounding.",
    );
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
