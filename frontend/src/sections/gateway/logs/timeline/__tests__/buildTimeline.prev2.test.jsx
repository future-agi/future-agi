import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import { buildTimeline } from "../buildTimeline";
import RequestTimeline from "../RequestTimeline";

// A log written before log entry v2 has no attempts, no generation id and
// attempt_count 1: the column's database default, not a count the gateway
// sent. Its timeline never reports that default as attempts. A v2 row keeps
// the count it recorded.

const T0 = "2026-10-20T09:30:45.000Z";

// As a hosted row from before log entry v2, or any log of a self-hosted
// install upgraded from 1.58.0, reads after the v2 columns were added.
const preV2 = (overrides) => ({
  started_at: T0,
  latency_ms: 420,
  status_code: 200,
  generation_id: null,
  attempt_count: 1,
  attempts: [],
  cache_hit: false,
  ...overrides,
});

const details = (events) => events.map((event) => event.detail || "");

describe("Timeline of a log written before log entry v2", () => {
  it("never reports the column's default as an attempt count", () => {
    const { events } = buildTimeline(preV2());
    expect(details(events).some((d) => /attempt\(s\) reported/.test(d))).toBe(
      false,
    );
    expect(events.find((e) => e.kind === "missing-attempts")).toMatchObject({
      label: "Attempt details not recorded",
      detail: "Upstream execution cannot be determined from this log",
    });
  });

  it("says a pre-v2 429 cannot show whether an upstream call was made", () => {
    const { events } = buildTimeline(
      preV2({
        status_code: 429,
        is_error: true,
        error_message: "rate limit exceeded",
      }),
    );
    expect(events.map((e) => e.kind)).toEqual([
      "received",
      "missing-attempts",
      "response",
    ]);
    expect(events[1].detail).toBe(
      "Upstream execution cannot be determined from this log",
    );
    expect(events[2].detail).toBe("");
  });

  it("says a pre-v2 cache hit made no upstream call", () => {
    const { events } = buildTimeline(preV2({ cache_hit: true }));
    expect(events.find((e) => e.kind === "cache")).toMatchObject({
      label: "Served from the gateway cache",
      detail: "No upstream call was made",
    });
  });

  it("shows no attempt count in the drawer's timeline", () => {
    render(<RequestTimeline log={preV2({ status_code: 502 })} />);
    const timeline = within(
      screen.getByRole("list", { name: "Request timeline" }),
    );
    expect(
      timeline.getByText("Attempt details not recorded"),
    ).toBeInTheDocument();
    expect(
      timeline.getByText(
        "Upstream execution cannot be determined from this log",
      ),
    ).toBeInTheDocument();
    expect(timeline.queryByText(/attempt\(s\) reported/)).toBeNull();
    expect(timeline.queryByText("Refused by the gateway")).toBeNull();
  });
});

describe("Timeline of a log entry v2 row", () => {
  it("keeps the attempt count the gateway recorded", () => {
    const { events } = buildTimeline(
      preV2({ generation_id: "gen-v2", attempt_count: 2, status_code: 502 }),
    );
    expect(events.find((e) => e.kind === "missing-attempts").detail).toBe(
      "2 attempt(s) reported; individual details are unavailable",
    );
  });

  it("still names a 429 the gateway refused with no attempt", () => {
    const { events } = buildTimeline(
      preV2({
        generation_id: "gen-v2",
        attempt_count: 0,
        status_code: 429,
        is_error: true,
        error_message: "budget exceeded",
      }),
    );
    expect(events.map((e) => [e.kind, e.label])).toEqual([
      ["received", "Received"],
      ["refused", "Refused by the gateway"],
      ["response", "Responded 429"],
    ]);
    expect(events[2].detail).toBe("No upstream call was made");
  });
});
