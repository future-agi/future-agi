import { describe, expect, it, vi } from "vitest";

vi.mock("../Mixpanel", () => ({
  resetUser: vi.fn(),
}));

import axiosInstance from "../axios";

describe("axios response shape", () => {
  it("does not add camelCase aliases to backend response keys", () => {
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

    expect(Object.keys(result.data)).toEqual(["created_at", "span_attributes"]);
    expect(result.data.createdAt).toBeUndefined();
    expect(Object.keys(result.data.span_attributes)).toEqual([
      "gen_ai.usage.total_tokens",
    ]);
    expect(
      result.data.span_attributes["genAi.usage.totalTokens"],
    ).toBeUndefined();
  });
});
