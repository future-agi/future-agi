import { describe, it, expect } from "vitest";

import {
  BROWSER_ENVIRONMENT_LINE,
  failureDetail,
  failureHeadline,
} from "../failureWords";

describe("a browser agent built without a browser environment", () => {
  const failure = {
    code: "browser_environment_required",
    domain: "environment",
    retryable: false,
    message:
      "This agent drives a web browser, so it runs only in an environment that gives it a browser and the sites it may open. This job names no sites.",
  };

  it("reads as the page's own words, which say what to do", () => {
    expect(failureHeadline(failure)).toBe(BROWSER_ENVIRONMENT_LINE);
    expect(BROWSER_ENVIRONMENT_LINE).toMatch(/browser/i);
    expect(BROWSER_ENVIRONMENT_LINE).toMatch(/sites/i);
    expect(BROWSER_ENVIRONMENT_LINE).not.toMatch(/isn't supported/i);
  });

  it("is the whole detail: the kit's message is not appended", () => {
    expect(failureDetail(failure)).toBe(BROWSER_ENVIRONMENT_LINE);
  });

  it("does not take over a failure with a cause", () => {
    expect(
      failureHeadline({ ...failure, cause: "agent_exited_before_ready" }),
    ).not.toBe(BROWSER_ENVIRONMENT_LINE);
  });
});
