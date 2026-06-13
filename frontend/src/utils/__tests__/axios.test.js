import { describe, expect, it, vi } from "vitest";

vi.mock("../Mixpanel", () => ({
  resetUser: vi.fn(),
}));

import axiosInstance from "../axios";
import { canonicalKeys, canonicalObject } from "../utils";

const runResponseInterceptor = (data) => {
  const fulfilled = axiosInstance.interceptors.response.handlers.find(
    (handler) => handler.fulfilled,
  )?.fulfilled;
  return fulfilled({ data }).data;
};

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

// The interceptor injects camelCase aliases into user-controlled `metadata`,
// which the gateway drawers render. canonicalObject removes those aliases so
// the original keys round-trip unchanged at every level.
describe("user-controlled metadata rendering (gateway drawers)", () => {
  it("interceptor injects phantom aliases into metadata, including nested", () => {
    const { metadata } = runResponseInterceptor({
      metadata: { user_id: "abc", nested: { inner_key: 1 } },
    });

    expect(metadata.userId).toBe("abc");
    expect(metadata.nested.innerKey).toBe(1);
  });

  it("canonicalObject strips aliases at every level for display", () => {
    const { metadata } = runResponseInterceptor({
      metadata: { user_id: "abc", nested: { inner_key: 1 } },
    });

    expect(canonicalObject(metadata)).toEqual({
      user_id: "abc",
      nested: { inner_key: 1 },
    });
  });

  it("preserves user-supplied camelCase keys that have no snake twin", () => {
    const { metadata } = runResponseInterceptor({
      metadata: { alreadyCamel: 1 },
    });

    expect(canonicalObject(metadata)).toEqual({ alreadyCamel: 1 });
  });
});
