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
    property_id: "system_attribute:traces:model",
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
    const filters = [
      {
        field: "model",
        property_id: "system_attribute:traces:model",
        operator: "is",
        value: "gpt-4o",
      },
    ];
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
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        timeout: expect.any(Number),
      }),
    );
    expect(mocks.post.mock.calls[0][2].timeout).toBeGreaterThan(0);
    expect(mocks.post.mock.calls[0][2].timeout).toBeLessThanOrEqual(
      SMART_AI_FILTER_TIMEOUT_MS,
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

  it("keeps the registry identity through multi-step field selection and values", async () => {
    const filters = [
      {
        field: "model",
        property_id: "system_attribute:traces:model",
        operator: "is",
        value: "gpt-4.1",
      },
    ];
    mocks.post
      .mockResolvedValueOnce({
        data: {
          result: { fields: ["system_attribute:traces:model"] },
        },
      })
      .mockResolvedValueOnce({ data: { result: { filters } } });
    const fetchValuesForFields = vi.fn().mockResolvedValue({
      "system_attribute:traces:model": ["gpt-4.1"],
    });
    const { result } = renderHook(() => useAIFilter(schema));

    let parsed;
    await act(async () => {
      parsed = await result.current.parseQuery("model gpt-4.1", {
        fetchValuesForFields,
      });
    });

    expect(parsed).toEqual(filters);
    expect(fetchValuesForFields).toHaveBeenCalledWith(
      ["system_attribute:traces:model"],
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        timeoutMs: expect.any(Number),
      }),
    );
    expect(mocks.post.mock.calls[0][1].schema).toEqual([
      expect.objectContaining({
        field: "model",
        property_id: "system_attribute:traces:model",
      }),
    ]);
    expect(mocks.post.mock.calls[1][1].schema).toEqual([
      expect.objectContaining({
        field: "model",
        property_id: "system_attribute:traces:model",
        choices: ["gpt-4.1"],
      }),
    ]);
    expect(mocks.post.mock.calls[0][2].signal).toBe(
      mocks.post.mock.calls[1][2].signal,
    );
    expect(mocks.post.mock.calls[1][2].timeout).toBeGreaterThan(0);
    expect(mocks.post.mock.calls[1][2].timeout).toBeLessThanOrEqual(
      mocks.post.mock.calls[0][2].timeout,
    );
  });

  it("fails closed on a legacy transport error instead of returning empty filters", async () => {
    mocks.post.mockRejectedValue(new Error("network failed"));
    const { result } = renderHook(() => useAIFilter(schema));

    let failure;
    await act(async () => {
      try {
        await result.current.parseQuery("model gpt-4o");
      } catch (error) {
        failure = error;
      }
    });

    expect(failure?.message).toBe("network failed");
    await waitFor(() => expect(result.current.error).toBe("network failed"));
  });

  it("rejects a malformed success response instead of treating it as empty", async () => {
    mocks.post.mockResolvedValue({ data: { result: {} } });
    const { result } = renderHook(() => useAIFilter(schema));

    await expect(
      act(async () => result.current.parseQuery("model gpt-4o")),
    ).rejects.toThrow("AI filter response omitted filters.");
  });

  it("enforces one action deadline even when the transport ignores abort", async () => {
    vi.useFakeTimers();
    try {
      mocks.post.mockImplementation(() => new Promise(() => {}));
      const { result } = renderHook(() => useAIFilter(schema));
      let failure;

      await act(async () => {
        const request = result.current
          .parseQuery("model gpt-4o")
          .catch((error) => {
            failure = error;
          });
        await vi.advanceTimersByTimeAsync(SMART_AI_FILTER_TIMEOUT_MS);
        await request;
      });

      expect(failure?.code).toBe("ai_filter_timeout");
      expect(mocks.post).toHaveBeenCalledTimes(1);
      expect(mocks.post.mock.calls[0][2].signal.aborted).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
