import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../Mixpanel", () => ({
  resetUser: vi.fn(),
}));

import axiosInstance from "../axios";

describe("axios response shape", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("keeps request bodies immutable and lets contract validation catch drift", () => {
    const fulfilled = axiosInstance.interceptors.request.handlers.find(
      (handler) => handler.fulfilled,
    )?.fulfilled;

    const body = {
      query_config: {
        time_range: { preset: "7D" },
        metrics: [{ name: "Latency", type: "system_metric" }],
      },
      queryConfig: {
        timeRange: { preset: "7D" },
        metrics: [{ name: "Latency", type: "system_metric" }],
      },
    };
    const config = {
      url: "/tracer/dashboard/7f9d0f16-9d42-48b6-9bb8-44cdb1a9c0ab/widgets/preview/",
      method: "post",
      data: body,
    };

    expect(() => fulfilled(config)).toThrowError(
      "request body contract validation failed",
    );
    expect(config.data).toEqual({
      query_config: {
        time_range: { preset: "7D" },
        metrics: [{ name: "Latency", type: "system_metric" }],
      },
      queryConfig: {
        timeRange: { preset: "7D" },
        metrics: [{ name: "Latency", type: "system_metric" }],
      },
    });
  });

  it("preserves canonical response keys without adding camelCase aliases", () => {
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
