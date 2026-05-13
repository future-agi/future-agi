import { describe, expect, it, vi } from "vitest";

vi.mock("../Mixpanel", () => ({
  resetUser: vi.fn(),
}));

import axiosInstance from "../axios";
import { canonicalKeys } from "../utils";

describe("axios response shape", () => {
  it("adds camelCase aliases while canonicalKeys still hides duplicates", () => {
    const fulfilled = axiosInstance.interceptors.response.handlers.find(
      (handler) => handler.fulfilled,
    )?.fulfilled;

    const response = {
      data: {
        created_at: "2026-05-13T00:00:00Z",
        span_attributes: {
          "gen_ai.usage.total_tokens": 42,
        },
      },
    };

    const result = fulfilled(response);

    expect(result.data.createdAt).toBe("2026-05-13T00:00:00Z");
    expect(result.data.spanAttributes).toBe(result.data.span_attributes);
    expect(result.data.span_attributes["genAi.usage.totalTokens"]).toBe(
      undefined,
    );
    expect(canonicalKeys(result.data)).toEqual([
      "created_at",
      "span_attributes",
    ]);
    expect(Object.keys(result.data.span_attributes)).toEqual([
      "gen_ai.usage.total_tokens",
    ]);
  });
});
