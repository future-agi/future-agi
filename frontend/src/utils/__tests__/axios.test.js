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

  it("runs response contract validation for documented error responses", async () => {
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
      expect.any(Object),
    );

    warn.mockRestore();
  });
});
