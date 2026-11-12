import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getGatewayAuthenticationErrorMessage,
  getInlineActionErrorMessage,
  getInlineLoadErrorMessage,
  getRequestErrorMessage,
  getSafeActionErrorMessage,
} from "../errorUtils";
import { handleError } from "../queryErrorHandler";
import { enqueueSnackbar } from "notistack";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

describe("getSafeActionErrorMessage", () => {
  const fallback = "Task could not be saved. Please retry.";

  it("keeps concise client validation feedback", () => {
    expect(
      getSafeActionErrorMessage(
        {
          response: {
            status: 400,
            data: { message: "allow_sampled: Unknown field." },
          },
        },
        fallback,
      ),
    ).toBe("allow_sampled: Unknown field.");
  });

  it.each([
    "Code: 159. DB::Exception: Timeout exceeded",
    "Timeout exceeded\nStack trace: SELECT secret FROM spans",
    "Traceback (most recent call last): internal module",
  ])("hides internal query details: %s", (message) => {
    expect(
      getSafeActionErrorMessage(
        { response: { status: 500, data: { result: message } } },
        fallback,
      ),
    ).toBe(fallback);
  });

  it("does not trust an internal-looking message even on a 400 response", () => {
    expect(
      getSafeActionErrorMessage(
        {
          response: {
            status: 400,
            data: { detail: "Code: 159 DB::Exception: Timeout exceeded" },
          },
        },
        fallback,
      ),
    ).toBe(fallback);
  });
});

describe("getSafeActionErrorMessage for typed user-facing refusals", () => {
  const fallback = "Something went wrong";
  const retry =
    "Could not verify your plan's dataset limit. Please try again in a moment.";

  it("shows the dataset limit check retry message despite its 503", () => {
    expect(
      getSafeActionErrorMessage(
        {
          response: {
            status: 503,
            data: { code: "dataset_limit_check_failed", message: retry },
          },
        },
        fallback,
      ),
    ).toBe(retry);
  });

  it("reads the code from the interceptor-flattened error too", () => {
    expect(
      getSafeActionErrorMessage(
        { statusCode: 503, code: "dataset_limit_check_failed", result: retry },
        fallback,
      ),
    ).toBe(retry);
  });

  it("still hides a 503 without a user-facing code", () => {
    expect(
      getSafeActionErrorMessage(
        {
          statusCode: 503,
          code: "service_unavailable",
          result: "upstream harness error: connection refused",
        },
        fallback,
      ),
    ).toBe(fallback);
  });

  it("still hides internal details behind a user-facing code", () => {
    expect(
      getSafeActionErrorMessage(
        {
          statusCode: 503,
          code: "dataset_limit_check_failed",
          result: "Code: 159. DB::Exception: Timeout exceeded",
        },
        fallback,
      ),
    ).toBe(fallback);
  });
});

describe("gateway authentication exact allowlist", () => {
  const remedy =
    "Gateway authentication failed. Ask your administrator to check the gateway credentials.";
  const classified = (message) => ({
    message: "Request failed with status code 401",
    response: {
      status: 401,
      data: { code: "gateway_request_rejected", message },
    },
  });
  beforeEach(() => vi.clearAllMocks());

  it.each([
    remedy,
    "private upstream credentials",
    ` ${remedy}`,
    `${remedy}\n`,
    "",
    null,
    { message: remedy },
  ])("uses only the fixed safe remedy for classified message %j", (message) => {
    const error = classified(message);
    const before = JSON.stringify(error);
    expect(getGatewayAuthenticationErrorMessage(error)).toBe(remedy);
    expect(getRequestErrorMessage(error, "old fallback")).toBe(remedy);
    expect(getSafeActionErrorMessage(error, "old fallback")).toBe(remedy);
    expect(JSON.stringify(error)).toBe(before);
    handleError(error);
    expect(enqueueSnackbar).toHaveBeenCalledWith(remedy, { variant: "error" });
  });

  it.each([
    { response: { status: 500, data: { code: "gateway_request_rejected" } } },
    { response: { status: 401, data: { code: "authentication_failed" } } },
    {
      response: {
        status: 401,
        data: { result: { code: "gateway_request_rejected" } },
      },
    },
    { statusCode: 401, code: "gateway_request_rejected" },
    { response: { status: "401", data: { code: "gateway_request_rejected" } } },
    null,
  ])(
    "does not classify another status or an untrusted code location: %j",
    (error) => {
      expect(getGatewayAuthenticationErrorMessage(error)).toBeNull();
      handleError(error);
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    },
  );

  it("retains local error ownership in the global cache handler", () => {
    handleError(classified(remedy), {
      options: { meta: { errorHandled: true } },
    });
    handleError(classified(remedy), undefined, undefined, {
      options: { meta: { errorHandled: true } },
    });
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
});

// F-r2-1: inline error UI (an Alert or an action-error line) must not show a
// workspace refusal the recovery handles. It shows one calm line instead of
// nothing, so a click never fails silently when the recovery ends without a
// line of its own.
describe("inline error text for a refusal the workspace recovery explains", () => {
  const refusal =
    "The selected workspace is unavailable. Select an accessible workspace and retry.";
  const handled = {
    code: "workspace_unavailable",
    statusCode: 403,
    message: refusal,
    result: refusal,
    handledBy: "workspace-recovery",
  };
  const unclaimed = { ...handled, handledBy: undefined };
  const validation = { statusCode: 400, result: "Choose another name" };
  const inlineAction = (error) =>
    getInlineActionErrorMessage(error, "Couldn't save");
  const inlineLoad = (error) =>
    getInlineLoadErrorMessage(error, "Could not load.");

  it("an action error shows one calm line for a handled refusal", () => {
    expect(inlineAction(handled)).toBe("Your workspace changed. Try again.");
  });

  it("an action error keeps the usual safe text otherwise", () => {
    expect(inlineAction(validation)).toBe("Choose another name");
    expect(inlineAction(unclaimed)).toBe("Couldn't save");
  });

  it("a load error says calmly that the workspace changed", () => {
    expect(inlineLoad(handled)).toBe(
      "Your workspace changed before this loaded.",
    );
    expect(inlineLoad(validation)).toBe("Choose another name");
    expect(inlineLoad(unclaimed)).toBe("Could not load.");
  });
});
