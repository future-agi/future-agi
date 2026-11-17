import { describe, expect, it } from "vitest";

import { buildTimeline } from "../buildTimeline";

const T0 = "2026-10-20T09:30:45.155605Z";

// The demo's 3-attempt fallback: a 502 on the primary, a 429 on the first
// model fallback, then a success on openai/gpt-4o-mini.
const FALLBACK = {
  started_at: T0,
  latency_ms: 470,
  status_code: 200,
  cache_hit: false,
  attempts: [
    {
      index: 1,
      kind: "primary",
      provider: "flaky-upstream",
      model: "gpt-4-turbo",
      status_code: 502,
      outcome: "error",
      error_class: "upstream_5xx",
      started_at: "2026-10-20T09:30:45.157177Z",
      latency_ms: 214,
      billable: false,
    },
    {
      index: 2,
      kind: "model_fallback",
      reason: "status_5xx",
      provider: "flaky-upstream",
      model: "gpt-4o",
      status_code: 429,
      outcome: "error",
      error_class: "rate_limited",
      started_at: "2026-10-20T09:30:45.371346Z",
      latency_ms: 250,
      billable: false,
    },
    {
      index: 3,
      kind: "model_fallback",
      reason: "status_429",
      provider: "openai",
      model: "gpt-4o-mini",
      status_code: 200,
      outcome: "success",
      started_at: "2026-10-20T09:30:45.621521Z",
      latency_ms: 2,
      billable: true,
      billed_cost_micros: 5,
    },
  ],
};

describe("buildTimeline", () => {
  it("places each attempt at its offset from the request start", () => {
    const { events, totalMs } = buildTimeline(FALLBACK);
    expect(totalMs).toBe(470);
    expect(events.map((e) => e.kind)).toEqual([
      "received",
      "attempt",
      "attempt",
      "attempt",
      "response",
    ]);
    const [, a1, a2, a3, done] = events;
    expect(a1).toMatchObject({ offsetMs: 2, durationMs: 214, tone: "error" });
    expect(a1.label).toBe("Primary · flaky-upstream / gpt-4-turbo");
    expect(a1.detail).toBe("502 · Upstream 5xx");
    expect(a2).toMatchObject({
      offsetMs: 216,
      durationMs: 250,
      tone: "warning",
    });
    expect(a2.label).toBe("Model fallback · flaky-upstream / gpt-4o");
    expect(a2.detail).toBe("429 · Rate limited · after Upstream error (5xx)");
    expect(a3).toMatchObject({ offsetMs: 466, durationMs: 2, tone: "healthy" });
    expect(done).toMatchObject({ offsetMs: 470, tone: "healthy" });
    expect(done.label).toBe("Responded 200");
  });

  it("keeps the axis wide enough for an attempt that ends after the recorded latency", () => {
    const { totalMs } = buildTimeline({
      ...FALLBACK,
      latency_ms: 100,
    });
    expect(totalMs).toBe(468);
  });

  it("shows an attempt with no recorded time as a point, said so", () => {
    const { events } = buildTimeline({
      started_at: "2026-10-20T10:10:05.514480Z",
      latency_ms: 8,
      status_code: 200,
      attempts: [
        {
          index: 1,
          kind: "primary",
          provider: "openrouter",
          model: "mistralai/ministral-8b-2512",
          status_code: 200,
          outcome: "success",
          started_at: "2026-10-20T10:10:05.521233Z",
          latency_ms: null,
          billable: true,
        },
      ],
    });
    const attempt = events.find((e) => e.kind === "attempt");
    expect(attempt).toMatchObject({ offsetMs: 7, durationMs: null });
    expect(attempt.detail).toBe("200 · time not recorded");
  });

  // A managed attempt names Future AGI
  // as its provider; the upstream it was bought from shows only in the
  // drawer's Attempt details. An own OpenRouter key keeps its own name.
  it("names a managed attempt's provider Future AGI, by credential, not by name", () => {
    const attempt = (credential) =>
      buildTimeline({
        started_at: T0,
        latency_ms: 8,
        status_code: 200,
        attempts: [
          {
            index: 1,
            kind: "primary",
            provider: "openrouter",
            model: "deepseek/deepseek-v4.1-flash",
            credential_type: credential,
            status_code: 200,
            outcome: "success",
            started_at: T0,
            latency_ms: 8,
            billable: true,
          },
        ],
      }).events.find((e) => e.kind === "attempt");
    expect(attempt("managed").label).toBe(
      "Primary · Future AGI / deepseek/deepseek-v4.1-flash",
    );
    expect(attempt("byok").label).toBe(
      "Primary · openrouter / deepseek/deepseek-v4.1-flash",
    );
  });

  it("explains a cache hit: no upstream call", () => {
    const { events } = buildTimeline({
      started_at: T0,
      latency_ms: 0,
      status_code: 200,
      cache_hit: true,
      attempts: [],
    });
    expect(events.map((e) => [e.kind, e.label])).toEqual([
      ["received", "Received"],
      ["cache", "Served from the gateway cache"],
      ["response", "Responded 200"],
    ]);
  });

  it("shows a guardrail block before any upstream call", () => {
    const { events } = buildTimeline({
      started_at: T0,
      latency_ms: 4,
      attempt_count: 0,
      status_code: 403,
      guardrail_triggered: true,
      guardrail_results: [
        {
          name: "keyword-blocklist",
          action: "block",
          score: 1,
          threshold: 0.5,
          message: "Blocked keywords found: internal only",
        },
      ],
      attempts: [],
    });
    expect(events.map((e) => e.kind)).toEqual([
      "received",
      "guardrail",
      "response",
    ]);
    expect(events[1]).toMatchObject({
      label: "Guardrail keyword-blocklist: blocked",
      detail: "Blocked keywords found: internal only",
      tone: "error",
    });
    expect(events[2]).toMatchObject({
      label: "Responded 403",
      detail: "No upstream call was made",
      tone: "error",
    });
  });

  it.each([undefined, null, 1, 2])(
    "does not infer refusal from missing attempt records (count %s)",
    (attemptCount) => {
      const { events } = buildTimeline({
        started_at: T0,
        status_code: 502,
        attempt_count: attemptCount,
        attempts: [],
      });
      expect(events.some((event) => event.kind === "refused")).toBe(false);
      expect(
        events.some((event) => event.label === "Attempt details not recorded"),
      ).toBe(true);
      expect(
        events.some((event) => event.detail?.includes("No upstream call")),
      ).toBe(false);
    },
  );

  it("does not turn an output guardrail block into proof of no upstream call", () => {
    const { events } = buildTimeline({
      started_at: T0,
      status_code: 403,
      attempt_count: 1,
      guardrail_results: [{ name: "output", action: "block" }],
      attempts: [],
    });
    expect(
      events.some((event) => event.detail === "No upstream call was made"),
    ).toBe(false);
  });

  it("names a request the gateway refused itself", () => {
    const { events } = buildTimeline({
      started_at: T0,
      latency_ms: 1,
      attempt_count: 0,
      status_code: 429,
      is_error: true,
      error_message:
        "tag 'workload:batch-eval' budget exceeded: spent $0.00 of $0.00 daily limit",
      attempts: [],
    });
    expect(events.map((e) => e.kind)).toEqual([
      "received",
      "refused",
      "response",
    ]);
    expect(events[1].detail).toMatch(/budget exceeded/);
    expect(events[2].tone).toBe("warning");
  });

  it("reads the guardrail object shape, and warns without blocking", () => {
    const { events } = buildTimeline({
      started_at: T0,
      latency_ms: 300,
      status_code: 200,
      guardrail_triggered: true,
      guardrail_results: {
        action: "warn",
        checks: [{ name: "pii", action: "warn" }],
      },
      attempts: [],
    });
    expect(events.find((e) => e.kind === "guardrail")).toMatchObject({
      label: "Guardrail pii: warned",
      tone: "warning",
    });
  });

  it("is empty for a log with no start time", () => {
    expect(buildTimeline({})).toEqual({ events: [], totalMs: 0 });
  });
});
