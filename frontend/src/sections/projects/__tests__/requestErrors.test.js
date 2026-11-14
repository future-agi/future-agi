import { describe, expect, it } from "vitest";
import {
  RETRY_LATER,
  actionErrorMessage,
  errorStatus,
  isServerFault,
} from "../requestErrors";

// A refusal as the app's axios hands it over: the server's error envelope,
// its message under detail, message, error and result, and its status.
const envelope = (statusCode, message) => ({
  status: false,
  code: "bad_request",
  detail: message,
  message,
  error: message,
  result: message,
  statusCode,
});

const words = {
  fallback: "Jev couldn't start. Check the question and try again.",
  retry: "Couldn't start right now. Try again in a moment.",
};

describe("a failed action's words", () => {
  it("reads the status the app's axios hands over, or a raw Axios error's", () => {
    expect(errorStatus({ statusCode: 503 })).toBe(503);
    expect(errorStatus({ response: { status: 409 } })).toBe(409);
    expect(errorStatus({ message: "Network Error" })).toBeNull();
  });

  it("doesn't blame the input for the server, or for no answer", () => {
    for (const error of [
      { statusCode: 503, message: "Database temporarily unavailable" },
      { statusCode: 500, message: "Internal Server Error" },
      { message: "Network Error", transportCode: "ERR_NETWORK" },
    ]) {
      expect(isServerFault(error)).toBe(true);
      expect(actionErrorMessage(error, words)).toBe(words.retry);
    }
    expect(actionErrorMessage({ statusCode: 500 }, { fallback: "x" })).toBe(
      RETRY_LATER,
    );
  });

  it("shows a refusal's own message, else the caller's words", () => {
    expect(
      actionErrorMessage(envelope(400, "That question is too long."), words),
    ).toBe("That question is too long.");
    expect(actionErrorMessage({ statusCode: 400 }, words)).toBe(words.fallback);
    expect(isServerFault({ statusCode: 422 })).toBe(false);
  });
});
