import { describe, it, expect } from "vitest";
import { FAILURE_TEXT, failureText } from "../versionLabels";

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
