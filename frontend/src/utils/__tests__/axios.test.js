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
      code: "ECONNABORTED",
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
      transportCode: "ECONNABORTED",
    });
  });

  it("preserves errors raised before a request receives a response", async () => {
    const rejected = axiosInstance.interceptors.response.handlers.find(
      (handler) => handler.rejected,
    )?.rejected;

    await expect(
      rejected({
        name: "ApiContractValidationError",
        message: "source: Required",
        config: {
          url: "/simulate/api/harness-jobs/preflight/",
          method: "post",
        },
      }),
    ).rejects.toMatchObject({ message: "source: Required" });
  });

  it("keeps a signed-out viewer on a shared link when it needs sign-in", async () => {
    const rejected = axiosInstance.interceptors.response.handlers.find(
      (handler) => handler.rejected,
    )?.rejected;
    window.history.pushState({}, "", "/shared/share-token");
    localStorage.removeItem("refreshToken");
    localStorage.removeItem("rememberMe");
    const config = { url: "/tracer/shared/share-token/", method: "get" };
    const error = {
      config,
      response: {
        status: 401,
        config,
        data: { status: false, message: "Authentication required" },
      },
    };

    await expect(rejected(error)).rejects.toMatchObject({ statusCode: 401 });
    expect(window.location.pathname).toBe("/shared/share-token");
    window.history.pushState({}, "", "/");
  });

  it("drops a dead session and retries a public shared link without it", async () => {
    const rejected = axiosInstance.interceptors.response.handlers.find(
      (handler) => handler.rejected,
    )?.rejected;
    window.history.pushState({}, "", "/shared/share-token");
    localStorage.setItem("accessToken", "stale");
    localStorage.removeItem("refreshToken");
    localStorage.removeItem("rememberMe");
    const sent = [];
    const config = {
      url: "/tracer/shared/share-token/",
      method: "get",
      headers: { Authorization: "Bearer stale" },
      adapter: async (retried) => {
        sent.push(retried.headers?.Authorization ?? null);
        return {
          data: { resource_type: "error_feed_issue" },
          status: 200,
          statusText: "OK",
          headers: {},
          config: retried,
        };
      },
    };
    const error = {
      config,
      response: { status: 401, config, data: { status: false } },
    };

    try {
      const response = await rejected(error);
      expect(response.data).toEqual({ resource_type: "error_feed_issue" });
      expect(sent).toEqual([null]);
      expect(localStorage.getItem("accessToken")).toBeNull();
    } finally {
      window.history.pushState({}, "", "/");
    }
  });
});
