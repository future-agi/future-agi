import { describe, expect, it, vi } from "vitest";

vi.mock("../Mixpanel", () => ({
  resetUser: vi.fn(),
}));

import axiosInstance from "../axios";
import { canonicalKeys } from "../utils";

describe("axios response shape", () => {
  it("strips response-added camelCase aliases before sending request bodies", () => {
    const fulfilled = axiosInstance.interceptors.request.handlers.find(
      (handler) => handler.fulfilled,
    )?.fulfilled;
    const choiceScores = { yes_no: 1 };

    const config = {
      url: "/model-hub/eval-templates/create-v2/",
      method: "post",
      data: {
        is_draft: true,
        isDraft: true,
        output_type: "pass_fail",
        outputType: "pass_fail",
        choice_scores: choiceScores,
        choiceScores,
        nested: {
          pass_threshold: 0.5,
          passThreshold: 0.5,
        },
      },
    };

    const result = fulfilled(config);

    expect(result.data).toEqual({
      is_draft: true,
      output_type: "pass_fail",
      choice_scores: { yes_no: 1 },
      nested: {
        pass_threshold: 0.5,
      },
    });
  });

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

  it("fails fast when documented error responses drift from the generated contract", async () => {
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

    warn.mockRestore();
  });
});
