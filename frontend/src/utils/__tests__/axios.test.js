import { afterEach, describe, expect, it, vi } from "vitest";

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

    expect(result.data.created_at).toBe("2026-05-13T00:00:00Z");
    expect(result.data.createdAt).toBeUndefined();
    expect(result.data.span_attributes).toEqual({
      "gen_ai.usage.total_tokens": 42,
    });
    expect(result.data.spanAttributes).toBeUndefined();
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

  it("warns by default when documented error responses drift from the generated contract", async () => {
    const rejected = axiosInstance.interceptors.response.handlers.find(
      (handler) => handler.rejected,
    )?.rejected;
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    const error = {
      config: { url: "/accounts/2fa/recovery-codes/", method: "get" },
      response: {
        status: 400,
        config: { url: "/accounts/2fa/recovery-codes/", method: "get" },
        data: "not-an-error-envelope",
      },
    };

    await expect(rejected(error)).rejects.toMatchObject({ statusCode: 400 });
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("response contract validation failed"),
      expect.objectContaining({ kind: "response" }),
    );
  });

  it("can fail fast on response drift when strict response contracts are enabled", async () => {
    vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");

    const rejected = axiosInstance.interceptors.response.handlers.find(
      (handler) => handler.rejected,
    )?.rejected;
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    const error = {
      config: { url: "/accounts/2fa/recovery-codes/", method: "get" },
      response: {
        status: 400,
        config: { url: "/accounts/2fa/recovery-codes/", method: "get" },
        data: "not-an-error-envelope",
      },
    };

    await expect(rejected(error)).rejects.toMatchObject({
      name: "ApiContractValidationError",
      details: { kind: "response" },
    });
    expect(warn).not.toHaveBeenCalled();
  });

  it("preserves the public API error envelope for callers", async () => {
    const rejected = axiosInstance.interceptors.response.handlers.find(
      (handler) => handler.rejected,
    )?.rejected;

    const errorEnvelope = {
      status: false,
      type: "validation_error",
      code: "required",
      detail: "name: This field is required.",
      message: "name: This field is required.",
      result: "name: This field is required.",
      attr: "name",
      details: { name: ["This field is required."] },
    };

    const error = {
      config: { url: "/accounts/2fa/recovery-codes/", method: "get" },
      response: {
        status: 400,
        config: { url: "/accounts/2fa/recovery-codes/", method: "get" },
        data: errorEnvelope,
      },
    };

    await expect(rejected(error)).rejects.toMatchObject({
      ...errorEnvelope,
      statusCode: 400,
    });
  });
});
