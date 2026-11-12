// CP P8 on the host: each Console error report becomes exactly one Sentry
// event tagged product=omega-console, carrying the request id and no content;
// vitals become a breadcrumb and a timed span; the host caps a flood.
import { describe, expect, it, vi } from "vitest";

import {
  CONSOLE_TELEMETRY_PRODUCT,
  MAX_ERRORS_PER_MINUTE,
  createConsoleTelemetry,
} from "../console-telemetry";

const ERROR = {
  type: "embed.telemetry",
  kind: "error",
  name: "load",
  routeTemplate: "/agents/:agentId",
  release: "r9-2803ad68",
  code: "http_502",
  requestId: "req_6f1c2b9e0a7d4c3b8e5f",
};
const VITAL = {
  type: "embed.telemetry",
  kind: "vital",
  name: "route-data",
  routeTemplate: "/agents",
  release: "r9-2803ad68",
  valueMs: 412,
};

const fakeSentry = () => {
  const span = { end: vi.fn() };
  return {
    span,
    captureEvent: vi.fn(),
    addBreadcrumb: vi.fn(),
    startInactiveSpan: vi.fn(() => span),
  };
};

describe("Console telemetry to Sentry", () => {
  it("sends one tagged event per error with the request id and nothing else", () => {
    const sentry = fakeSentry();
    const telemetry = createConsoleTelemetry({ sentry });
    expect(telemetry.report(ERROR)).toBe(true);
    expect(sentry.captureEvent).toHaveBeenCalledTimes(1);
    const [event] = sentry.captureEvent.mock.calls[0];
    expect(event).toEqual({
      message: "Omega Console error (req_6f1c2b9e0a7d4c3b8e5f)",
      level: "error",
      tags: {
        product: CONSOLE_TELEMETRY_PRODUCT,
        "omega.name": "load",
        "omega.code": "http_502",
        "omega.route": "/agents/:agentId",
        "omega.release": "r9-2803ad68",
        "omega.request_id": "req_6f1c2b9e0a7d4c3b8e5f",
      },
      fingerprint: ["omega-console", "load", "http_502", "/agents/:agentId"],
    });
    // The message never names the code, so FA's ignoreErrors ("Loading
    // chunk", "Failed to fetch") cannot drop a chunk or network report.
    const chunk = createConsoleTelemetry({ sentry: fakeSentry() });
    expect(chunk.report({ ...ERROR, name: "chunk", code: "chunk_load" })).toBe(
      true,
    );
  });

  it("sends a repeated report once, and keeps reports of other requests apart", () => {
    const sentry = fakeSentry();
    const telemetry = createConsoleTelemetry({ sentry });
    telemetry.report(ERROR);
    telemetry.report(ERROR);
    telemetry.report({ ...ERROR, requestId: "req_000000000000000000002" });
    const { requestId, ...noId } = ERROR;
    expect(requestId).toBeTruthy();
    telemetry.report(noId);
    const messages = sentry.captureEvent.mock.calls.map(([e]) => e.message);
    expect(messages).toEqual([
      "Omega Console error (req_6f1c2b9e0a7d4c3b8e5f)",
      "Omega Console error (req_000000000000000000002)",
      "Omega Console error",
    ]);
    // All three group into one issue.
    const prints = sentry.captureEvent.mock.calls.map(([e]) => e.fingerprint);
    expect(new Set(prints.map(String)).size).toBe(1);
  });

  it("caps errors at the host's limit per rolling minute", () => {
    let clock = 0;
    const sentry = fakeSentry();
    const telemetry = createConsoleTelemetry({ sentry, now: () => clock });
    for (let i = 0; i < MAX_ERRORS_PER_MINUTE + 5; i += 1) {
      telemetry.report({
        ...ERROR,
        requestId: `req_flood_${String(i).padStart(4, "0")}`,
      });
    }
    expect(sentry.captureEvent).toHaveBeenCalledTimes(MAX_ERRORS_PER_MINUTE);
    clock += 60_000;
    telemetry.report({ ...ERROR, requestId: "req_after_the_minute" });
    expect(sentry.captureEvent).toHaveBeenCalledTimes(
      MAX_ERRORS_PER_MINUTE + 1,
    );
  });

  it("records a vital as a breadcrumb and a span of its length ending now", () => {
    const sentry = fakeSentry();
    const telemetry = createConsoleTelemetry({ sentry, now: () => 10_000 });
    expect(telemetry.report(VITAL)).toBe(true);
    expect(sentry.addBreadcrumb).toHaveBeenCalledWith({
      category: "omega-console.vital",
      message: "route-data",
      level: "info",
      data: {
        product: "omega-console",
        "omega.route": "/agents",
        "omega.release": "r9-2803ad68",
        "omega.value_ms": 412,
      },
    });
    expect(sentry.startInactiveSpan).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "omega-console route-data",
        op: "ui.omega-console.vital",
        startTime: 10_000 - 412,
        forceTransaction: true,
      }),
    );
    expect(sentry.span.end).toHaveBeenCalledWith(10_000);
    expect(sentry.captureEvent).not.toHaveBeenCalled();
  });

  it("ignores anything that is not an error or a vital, and never throws", () => {
    const sentry = fakeSentry();
    sentry.captureEvent.mockImplementation(() => {
      throw new Error("tracker down");
    });
    const telemetry = createConsoleTelemetry({ sentry });
    expect(telemetry.report({ ...ERROR, kind: "event" })).toBe(false);
    expect(telemetry.report(null)).toBe(false);
    expect(() => telemetry.report(ERROR)).not.toThrow();
  });
});
