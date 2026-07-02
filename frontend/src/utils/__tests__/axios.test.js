import { describe, expect, it, vi } from "vitest";

vi.mock("../Mixpanel", () => ({
  resetUser: vi.fn(),
}));

import axiosInstance from "../axios";
import { canonicalKeys } from "../canonicalKeys";
import { isGeneratedCamelAlias } from "../responseAliasMetadata";

describe("axios response shape Unit", () => {
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
    expect(isGeneratedCamelAlias(result.data, "createdAt")).toBe(false);
  });

  it("marks generated metadata aliases while preserving enumerable compatibility", () => {
    const fulfilled = axiosInstance.interceptors.response.handlers.find(
      (handler) => handler.fulfilled,
    )?.fulfilled;

    const response = {
      data: {
        metadata: {
          generated_key: "generated",
          explicit_key: "same-value",
          explicitKey: "same-value",
          events: [{ request_id: "req-1" }],
        },
      },
    };

    const result = fulfilled(response);

    expect(result.data.metadata.generatedKey).toBe("generated");
    expect(result.data.metadata.events[0].requestId).toBe("req-1");
    expect(Object.keys(result.data.metadata)).toEqual([
      "generated_key",
      "explicit_key",
      "explicitKey",
      "events",
      "generatedKey",
    ]);
    expect(Object.keys(result.data.metadata.events[0])).toEqual([
      "request_id",
      "requestId",
    ]);
    expect(JSON.stringify(result.data.metadata)).toContain("generatedKey");
    expect(JSON.stringify(result.data.metadata)).toContain("requestId");
    expect({ ...result.data.metadata }.generatedKey).toBe("generated");

    expect(isGeneratedCamelAlias(result.data.metadata, "generatedKey")).toBe(
      true,
    );
    expect(isGeneratedCamelAlias(result.data.metadata, "explicitKey")).toBe(
      false,
    );
    expect(
      isGeneratedCamelAlias(result.data.metadata.events[0], "requestId"),
    ).toBe(true);
  });
});
