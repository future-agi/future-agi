import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../Mixpanel", () => ({
  resetUser: vi.fn(),
}));

import axiosInstance from "../axios";
import { nonRetryableQueryErrorMessage } from "../queryReadState";

const TOO_BROAD_MESSAGE =
  "The graph cannot apply this Tag filter: too many traces and spans were " +
  "tagged with it in the app. The list below is still filtered by it.";

const GRAPH_URL = "/tracer/trace/get_graph_methods/";

// What the app's own callers receive: the raw Axios error run through the
// shared response interceptor in src/utils/axios.
const throughInterceptor = async (status, data) => {
  const rejected = axiosInstance.interceptors.response.handlers.find(
    (handler) => handler.rejected,
  )?.rejected;
  const config = { url: GRAPH_URL, method: "post" };
  try {
    await rejected({
      code: "ERR_BAD_REQUEST",
      config,
      response: { status, config, data },
    });
  } catch (error) {
    return error;
  }
  throw new Error("the interceptor did not reject");
};

describe("nonRetryableQueryErrorMessage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads the too-broad copy from the interceptor's flattened error", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const error = await throughInterceptor(400, {
      status: false,
      code: "tag_filter_too_broad",
      message: TOO_BROAD_MESSAGE,
    });

    expect(error.response).toBeUndefined();
    expect(error.statusCode).toBe(400);
    expect(nonRetryableQueryErrorMessage(error)).toBe(TOO_BROAD_MESSAGE);
  });

  it("still reads a raw Axios error", () => {
    expect(
      nonRetryableQueryErrorMessage({
        message: "Request failed with status code 400",
        code: "ERR_BAD_REQUEST",
        response: {
          status: 400,
          data: {
            status: false,
            code: "tag_filter_too_broad",
            message: TOO_BROAD_MESSAGE,
          },
        },
      }),
    ).toBe(TOO_BROAD_MESSAGE);
  });

  it("leaves every other error to the generic retry copy", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const otherCode = await throughInterceptor(400, {
      status: false,
      code: "bad_request",
      message: "Graph filter configuration is invalid",
    });
    const unavailable = await throughInterceptor(503, {
      status: false,
      code: "tag_assignment_unavailable",
      message: "Tags are temporarily unavailable.",
    });
    const withoutCopy = await throughInterceptor(400, {
      status: false,
      code: "tag_filter_too_broad",
    });

    expect(nonRetryableQueryErrorMessage(otherCode)).toBeNull();
    expect(nonRetryableQueryErrorMessage(unavailable)).toBeNull();
    expect(nonRetryableQueryErrorMessage(withoutCopy)).toBeNull();
    expect(
      nonRetryableQueryErrorMessage({
        message: "Network Error",
        code: "ERR_NETWORK",
      }),
    ).toBeNull();
    expect(nonRetryableQueryErrorMessage(undefined)).toBeNull();
  });
});
