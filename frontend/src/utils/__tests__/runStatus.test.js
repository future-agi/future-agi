import { describe, it, expect } from "vitest";
import { runStatusOf } from "../runStatus";
import {
  getStatusDetails,
  getStatusConfig,
  STATUS_CONFIG,
  STATUS_TYPES,
} from "../statusUtils";

describe("shared design-system status truth", () => {
  it.each([
    ["running", "info"],
    ["In progress", "info"],
    ["cancelled", "neutral"],
    ["canceled", "neutral"],
    ["stopped", "neutral"],
    ["passed", "success"],
    ["failed", "danger"],
    ["not-run", "neutral"],
    ["completed", "neutral"],
    ["disconnected", "neutral"],
    ["unknown", "neutral"],
    [null, "neutral"],
    [undefined, "neutral"],
    ["__proto__", "neutral"],
    ["constructor", "neutral"],
    ["token", "neutral"],
  ])("%s uses %s", (value, role) => expect(runStatusOf(value).role).toBe(role));
  it("preserves legacy null, absent, label and terminal classification contracts", () => {
    expect(getStatusDetails({ status: null }).finalStatus).toBe(
      STATUS_TYPES.UNSET,
    );
    expect(getStatusDetails({}).finalStatus).toBe(STATUS_TYPES.ERROR);
    expect(getStatusDetails({ status: "run failed" }).finalStatus).toBe(
      STATUS_TYPES.ERROR,
    );
    expect(getStatusDetails({ status: "in progress" }).finalStatus).toBe(
      STATUS_TYPES.RUNNING,
    );
    expect(
      getStatusDetails({ status: "canceled", label: "Stopped by you" })
        .finalLabel,
    ).toBe("Stopped by you");
    expect(getStatusDetails({ label: "pass" }).finalLabel).toBe("Status: PASS");
  });
  it("derives every legacy color from the canonical role", () => {
    Object.entries(STATUS_CONFIG).forEach(([status, config]) => {
      expect(config.textColor).toBe(`status.${runStatusOf(status).role}.fg`);
    });
    expect(getStatusConfig("unknown")).toEqual(STATUS_CONFIG.unset);
  });
});

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

// Both classifiers meet here: the candidate's neutral words for stopped work
// and the negative-first order for failures.
describe("runStatusOf free-form values", () => {
  it.each([
    ["stopped_running", "neutral"],
    ["cancelled_run", "neutral"],
    ["cancel_failed", "danger"],
    ["Inactive", "neutral"],
  ])("%o -> %s", (status, role) => {
    expect(runStatusOf(status).role).toBe(role);
  });
});
