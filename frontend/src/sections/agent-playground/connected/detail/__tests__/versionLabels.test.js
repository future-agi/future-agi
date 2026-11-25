import { describe, it, expect } from "vitest";
import {
  CHECK_WAIT_LIMIT_MS,
  FAILURE_TEXT,
  canRetry,
  failureText,
  missingCredentials,
} from "../versionLabels";

describe("failureText", () => {
  it.each([
    "agent_reading_timed_out",
    "agent_reading_crashed",
    "agent_reading_invalid",
    "agent_reading_model_error",
    "agent_reading_incomplete",
    "agent_reading_too_large",
    "agent_document_invalid",
    "reading_source_empty",
    "reading_job_invalid",
    "reading_output_not_empty",
  ])("shows the page's own words for %s, not the reading's message", (code) => {
    const text = failureText({
      code,
      message: "RuntimeError: provider said 401 for key sk-live-123",
    });
    expect(text).toBe(FAILURE_TEXT[code]);
    expect(text).not.toMatch(/sk-live|RuntimeError/);
  });

  it("never tells a reader to try again, since Retry shows only when it can", () => {
    const readingCodes = Object.keys(FAILURE_TEXT).filter(
      (code) => code.startsWith("agent_") || code.startsWith("reading_"),
    );
    readingCodes
      .filter((code) => code !== "agent_reading_unavailable")
      .forEach((code) => expect(FAILURE_TEXT[code]).not.toMatch(/try again/i));
  });

  it("falls back to the reading's message for a code it doesn't know", () => {
    expect(failureText({ code: "something_new", message: "It broke." })).toBe(
      "It broke.",
    );
    expect(failureText({ code: "something_new" })).toBe("something_new");
    expect(failureText(null)).toBe("");
  });
});

describe("canRetry", () => {
  const now = Date.parse("2026-11-18T12:00:00Z");
  const at = (ms) => new Date(now - ms).toISOString();

  it("offers Retry on a check pending past the server's wait limit", () => {
    expect(
      canRetry(
        { check: "pending", createdAt: at(CHECK_WAIT_LIMIT_MS + 1000) },
        now,
      ),
    ).toBe(true);
  });

  it("waits on a pending check restarted within the limit", () => {
    expect(
      canRetry(
        {
          check: "pending",
          createdAt: at(3 * CHECK_WAIT_LIMIT_MS),
          checkStartedAt: at(60 * 1000),
        },
        now,
      ),
    ).toBe(false);
    expect(canRetry({ check: "pending" }, now)).toBe(false);
  });

  it("offers Retry on a failed check unless the failure says it can't help", () => {
    expect(canRetry({ check: "failed", checkFailure: null }, now)).toBe(true);
    expect(
      canRetry({ check: "failed", checkFailure: { retryable: false } }, now),
    ).toBe(false);
  });
});

describe("missingCredentials", () => {
  it("names each credential a check found missing", () => {
    expect(
      missingCredentials({
        code: "credentials_missing",
        message: "Missing credentials: API_KEY, OTHER_TOKEN",
        missing: ["API_KEY", "OTHER_TOKEN"],
      }),
    ).toEqual(["API_KEY", "OTHER_TOKEN"]);
  });

  it("names none for any other failure", () => {
    expect(
      missingCredentials({ code: "agent_reading_crashed", message: "x, y" }),
    ).toEqual([]);
    expect(missingCredentials(null)).toEqual([]);
  });
});
