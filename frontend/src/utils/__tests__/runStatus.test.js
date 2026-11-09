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
