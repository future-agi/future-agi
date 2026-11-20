import { afterEach, describe, expect, it, vi } from "vitest";
import * as Sentry from "@sentry/react";

vi.mock("@sentry/react", () => ({
  addBreadcrumb: vi.fn(),
  captureException: vi.fn(),
  captureMessage: vi.fn(),
}));
vi.mock("../../config-global", () => ({ CURRENT_ENVIRONMENT: "production" }));

import logger from "../logger";

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("logger monitoring without Replay", () => {
  it("still captures application exceptions", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const error = new Error("example");
    logger.error("Application failed", error);
    expect(Sentry.captureException).toHaveBeenCalledWith(
      error,
      expect.objectContaining({ tags: { level: "error", source: "logger" } }),
    );
  });

  it("still captures error messages", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    logger.error("Application failed", { attempt: 1 });
    expect(Sentry.captureMessage).toHaveBeenCalledWith(
      "Application failed",
      expect.objectContaining({
        level: "error",
        extra: { data: { attempt: 1 } },
      }),
    );
  });

  it("records a Replay load warning as a breadcrumb, not an error event", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    logger.warn("Failed to load Sentry Replay:", new Error("unavailable"));
    expect(Sentry.addBreadcrumb).toHaveBeenCalledWith(
      expect.objectContaining({ category: "warning", level: "warning" }),
    );
    expect(Sentry.captureException).not.toHaveBeenCalled();
    expect(Sentry.captureMessage).not.toHaveBeenCalled();
  });
});
