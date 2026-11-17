import { describe, expect, it } from "vitest";
import {
  VERSIONING_STATE,
  versioningBackfillFailed,
  versioningReadable,
  versioningState,
} from "../versions";

// env.versioning.state is "off" | "enabling" | "ready". An older server sends
// only `enabled`, and a read that answered 404 versioning_disabled is off.
describe("versioningState", () => {
  it("reads the state the server sends", () => {
    for (const state of ["off", "enabling", "ready"]) {
      expect(versioningState({ versioning: { enabled: state !== "off", state } })).toBe(state);
    }
  });

  it("keeps off as off even when the flag looks on", () => {
    expect(versioningState({ versioning: { enabled: true, state: "off" } })).toBe(
      VERSIONING_STATE.OFF,
    );
  });

  it("reads an older server from enabled: on is ready, anything else off", () => {
    expect(versioningState({ versioning: { enabled: true } })).toBe("ready");
    expect(versioningState({ versioning: { enabled: false } })).toBe("off");
    expect(versioningState({ versioning: null })).toBe("off");
    expect(versioningState({})).toBe("off");
    expect(versioningState(undefined)).toBe("off");
    expect(versioningState({ versioning: { enabled: true, state: "backfilling" } })).toBe(
      "ready",
    );
  });

  it("is off when a read answered 404 versioning_disabled", () => {
    const disabled = { statusCode: 404, error: "versioning_disabled" };
    expect(versioningState({ versioning: { enabled: true, state: "ready" } }, disabled)).toBe(
      "off",
    );
    expect(
      versioningState({ versioning: { enabled: true, state: "ready" } }, { statusCode: 500 }),
    ).toBe("ready");
  });

  it("sends no versioning read while off, and reads while enabling or ready", () => {
    expect(versioningReadable({ versioning: { enabled: false, state: "off" } })).toBe(false);
    expect(versioningReadable({})).toBe(false);
    expect(versioningReadable({ versioning: { enabled: true, state: "enabling" } })).toBe(true);
    expect(versioningReadable({ versioning: { enabled: true, state: "ready" } })).toBe(true);
  });
});

// env.versioning.backfill (fi-cli step 2, 39c9a9fa): this environment's own state, beside the organization's
// cumulative progress, which never labels this environment failed.
describe("versioningBackfillFailed", () => {
  const backfill = (state, extra = {}) => ({ state, outcome_code: null, updated_at: null, progress: null, ...extra });
  const env = (b, readiness = "enabling") => ({ versioning: { enabled: true, state: readiness, backfill: b } });

  it("is true only when this environment's own backfill failed", () => {
    expect(versioningBackfillFailed(env(backfill("failed", { outcome_code: "interrupted" })))).toBe(true);
    for (const state of ["not_started", "running", "complete", "off"]) {
      expect(versioningBackfillFailed(env(backfill(state)))).toBe(false);
    }
  });

  it("claims no failure from the organization's progress, or from a server without the field", () => {
    const orgFailed = { state: "failed", total: 9, processed: 9, succeeded: 3, failed: 6, skipped: 0, heartbeat_at: null, updated_at: "2026-11-14T20:00:00Z" };
    expect(versioningBackfillFailed(env(backfill("running", { progress: orgFailed })))).toBe(false);
    expect(versioningBackfillFailed({ versioning: { enabled: true, state: "enabling" } })).toBe(false);
    expect(versioningBackfillFailed(undefined)).toBe(false);
  });

  it("leaves readiness alone: a ready environment stays ready after an older failure", () => {
    expect(versioningState(env(backfill("failed", { outcome_code: "error" }), "ready"))).toBe(VERSIONING_STATE.READY);
  });
});
