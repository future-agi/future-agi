import { describe, expect, it } from "vitest";

import {
  NOT_RETRIED_NOTE,
  RETRY_NOTE,
  WEBHOOK_EVENTS,
  deliveryNote,
  deliveryState,
  latestDeliveryByWebhook,
  needsNotRetriedNote,
  needsRetryNote,
  onlyUnproduced,
  unproducedNotes,
} from "../webhookEvents";

describe("webhook events", () => {
  // Request logging sends budget.exceeded, and a finished batch job sends
  // batch.completed once, so every offered event is produced.
  it("lists every event the console offers, each one produced", () => {
    const produced = Object.fromEntries(
      WEBHOOK_EVENTS.map((event) => [event.value, event.produced]),
    );
    expect(produced).toEqual({
      "request.completed": true,
      "error.occurred": true,
      "guardrail.triggered": true,
      "budget.exceeded": true,
      "batch.completed": true,
    });
  });

  it("names no not-sent note, since the gateway sends every offered event", () => {
    expect(
      unproducedNotes([
        "request.completed",
        "error.occurred",
        "guardrail.triggered",
        "budget.exceeded",
        "batch.completed",
      ]),
    ).toEqual([]);
    expect(unproducedNotes(undefined)).toEqual([]);
  });

  it("calls a webhook on budget.exceeded or batch.completed alone", () => {
    expect(onlyUnproduced(["budget.exceeded"])).toBe(false);
    expect(onlyUnproduced(["budget.exceeded", "batch.completed"])).toBe(false);
    expect(onlyUnproduced(["batch.completed"])).toBe(false);
    expect(onlyUnproduced([])).toBe(false);
  });
});

describe("delivery state", () => {
  it("reads a delivered event as delivered", () => {
    expect(deliveryState({ status: "delivered", attempts: 1 })).toMatchObject({
      tone: "healthy",
      label: "Delivered",
      awaitingRetry: false,
    });
  });

  it("reads a pending event that never ran as queued", () => {
    expect(deliveryState({ status: "pending", attempts: 0 })).toMatchObject({
      tone: "pending",
      label: "Queued",
      awaitingRetry: false,
    });
  });

  // The delivery schedule sends it once its retry time has passed.
  it("reads a failed attempt left pending as one that will be retried", () => {
    const state = deliveryState({
      status: "pending",
      attempts: 1,
      last_error: "HTTP 503: unavailable",
      next_retry_at: "2026-10-20T10:00:00Z",
    });
    expect(state).toMatchObject({ tone: "warning", awaitingRetry: true });
    expect(state.label).toBe("Failed, will retry");
  });

  it("reads failed and dead-letter events as not delivered", () => {
    expect(deliveryState({ status: "failed", attempts: 1 })).toMatchObject({
      tone: "error",
      label: "Failed",
    });
    expect(deliveryState({ status: "dead_letter", attempts: 5 })).toMatchObject(
      { tone: "error", label: "Gave up" },
    );
  });

  // Only a pending delivery whose attempt failed is sent again by the
  // delivery schedule. A Failed delivery (a failed Send test, or a redirect,
  // recorded once) and a Gave up one wait for Retry now.
  it("asks for the retry note only when a delivery will be retried", () => {
    expect(needsRetryNote([{ status: "delivered", attempts: 1 }])).toBe(false);
    expect(needsRetryNote([{ status: "pending", attempts: 0 }])).toBe(false);
    // A first attempt being sent has a next_retry_at too (its lease).
    expect(
      needsRetryNote([
        {
          status: "pending",
          attempts: 0,
          next_retry_at: "2026-10-20T09:31:00Z",
        },
      ]),
    ).toBe(false);
    expect(
      needsRetryNote([
        { status: "delivered", attempts: 1 },
        { status: "failed", attempts: 1 },
      ]),
    ).toBe(false);
    expect(needsRetryNote([{ status: "dead_letter", attempts: 5 }])).toBe(
      false,
    );
    expect(
      needsRetryNote([{ status: "pending", attempts: 2, last_error: "x" }]),
    ).toBe(true);
    expect(needsRetryNote(undefined)).toBe(false);
  });

  it("asks for the not-retried note when a delivery is Failed or Gave up", () => {
    expect(needsNotRetriedNote([{ status: "failed", attempts: 1 }])).toBe(true);
    expect(needsNotRetriedNote([{ status: "dead_letter", attempts: 5 }])).toBe(
      true,
    );
    expect(
      needsNotRetriedNote([
        { status: "delivered", attempts: 1 },
        { status: "pending", attempts: 2, last_error: "x" },
      ]),
    ).toBe(false);
    expect(needsNotRetriedNote(undefined)).toBe(false);
  });

  it("gives each delivery the note that is true of it", () => {
    expect(
      deliveryNote({ status: "pending", attempts: 1, last_error: "HTTP 503" }),
    ).toEqual({ kind: "retry", text: RETRY_NOTE });
    expect(deliveryNote({ status: "failed", attempts: 1 })).toEqual({
      kind: "not-retried",
      text: NOT_RETRIED_NOTE,
    });
    expect(deliveryNote({ status: "dead_letter", attempts: 5 })).toEqual({
      kind: "not-retried",
      text: NOT_RETRIED_NOTE,
    });
    expect(deliveryNote({ status: "delivered", attempts: 1 })).toBeNull();
    expect(deliveryNote({ status: "pending", attempts: 0 })).toBeNull();
    expect(RETRY_NOTE).toMatch(/sent again automatically/);
    expect(RETRY_NOTE).not.toMatch(/Retry now/);
    expect(NOT_RETRIED_NOTE).toMatch(/^Not retried automatically\./);
    expect(NOT_RETRIED_NOTE).toMatch(/Retry now/);
  });

  it("finds each webhook's latest delivery", () => {
    const latest = latestDeliveryByWebhook([
      { id: "a", webhook: "w1", created_at: "2026-10-20T09:30:00Z" },
      { id: "b", webhook: "w1", created_at: "2026-10-20T09:38:31Z" },
      { id: "c", webhook: "w2", created_at: "2026-10-20T08:00:00Z" },
    ]);
    expect(latest.w1.id).toBe("b");
    expect(latest.w2.id).toBe("c");
    expect(latestDeliveryByWebhook(undefined)).toEqual({});
  });
});
