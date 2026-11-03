import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as Sentry from "@sentry/react";
import { endActionTimer, startActionTimer } from "../actionTimer";

vi.mock("@sentry/react", () => ({ startInactiveSpan: vi.fn() }));

const makeSpan = () => ({ end: vi.fn() });

describe("actionTimer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-28T10:00:00Z"));
    vi.mocked(Sentry.startInactiveSpan).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("reports one transaction from the start time to the end", () => {
    const span = makeSpan();
    Sentry.startInactiveSpan.mockReturnValue(span);
    const startedAt = Date.now();

    startActionTimer("observe.filter.apply", { filter_count: 1 });
    vi.advanceTimersByTime(800);
    endActionTimer("observe.filter.apply");

    expect(Sentry.startInactiveSpan).toHaveBeenCalledWith({
      name: "observe.filter.apply",
      op: "ui.action",
      forceTransaction: true,
      startTime: startedAt / 1000,
      attributes: { filter_count: 1, outcome: "ok" },
    });
    expect(span.end).toHaveBeenCalledTimes(1);
  });

  it("sends nothing for an end with no matching start", () => {
    endActionTimer("never.started");
    expect(Sentry.startInactiveSpan).not.toHaveBeenCalled();
  });

  it("reports an action only once", () => {
    Sentry.startInactiveSpan.mockReturnValue(makeSpan());
    startActionTimer("a");
    endActionTimer("a");
    endActionTimer("a");
    expect(Sentry.startInactiveSpan).toHaveBeenCalledTimes(1);
  });

  it("times the latest start when the same action starts again", () => {
    Sentry.startInactiveSpan.mockReturnValue(makeSpan());
    startActionTimer("a", { n: 1 });
    vi.advanceTimersByTime(500);
    const secondStart = Date.now();
    startActionTimer("a", { n: 2 });
    endActionTimer("a", "error");

    expect(Sentry.startInactiveSpan).toHaveBeenCalledTimes(1);
    expect(Sentry.startInactiveSpan).toHaveBeenCalledWith(
      expect.objectContaining({
        startTime: secondStart / 1000,
        attributes: { n: 2, outcome: "error" },
      }),
    );
  });

  it("drops an action that was never answered within 30 seconds", () => {
    startActionTimer("a");
    vi.advanceTimersByTime(30001);
    endActionTimer("a");
    expect(Sentry.startInactiveSpan).not.toHaveBeenCalled();
  });
});
