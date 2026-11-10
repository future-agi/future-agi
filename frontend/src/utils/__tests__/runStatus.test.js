import { describe, it, expect } from "vitest";
import { runStatusOf } from "../runStatus";

describe("runStatusOf role", () => {
  it.each([
    // Negated or partial forms of a success word never read as green.
    ["incomplete", "warning"],
    ["unresolved", "warning"],
    ["Partially Completed", "warning"],
    ["partial_success", "warning"],
    ["unsuccessful", "danger"],
    ["unhealthy", "danger"],
    ["not_connected", "neutral"],
    ["not_ok", "neutral"],
    // Not started reads as waiting, like queued and pending.
    ["not_started", "neutral"],
    ["NotStarted", "neutral"],
    ["not-started", "neutral"],
    // Work in progress reads like running.
    ["Evaluating", "info"],
    ["editing", "info"],
    ["finalizing", "info"],
    ["reprocessing", "info"],
    // camelCase reaches the map entry.
    ["InProgress", "info"],
    ["needsReview", "warning"],
    // Existing positives and map entries are unchanged.
    ["passed", "success"],
    ["Healthy", "success"],
    ["resolved", "success"],
    ["connected", "success"],
    ["auto_resolved", "success"],
    ["OK", "success"],
    ["completed", "neutral"],
    ["disconnected", "neutral"],
    ["validation_error", "danger"],
    ["", "neutral"],
  ])("%o -> %s", (status, role) => {
    expect(runStatusOf(status).role).toBe(role);
  });
});
