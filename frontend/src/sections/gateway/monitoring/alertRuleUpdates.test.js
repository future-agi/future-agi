import { describe, expect, it } from "vitest";

import { buildAlertRulePatch } from "./alertRuleUpdates";

const rule = {
  name: "errors",
  metric: "error_count",
  threshold: 5,
  channels: ["ops"],
  enabled: true,
  custom: { nested: [1, null, { future: true }] },
};

const configWith = (rules) => ({
  version: 12,
  alerting: { enabled: false, rules, channels: [{ name: "ops", type: "log" }] },
});

describe("targeted alert rule updates", () => {
  it("edits only the target in the latest array, preserving order, other rules and unknown fields", () => {
    const other = { name: "cost", threshold: 200, custom: [null, { new: true }] };
    const latestRule = { ...rule, newServerField: { keep: true } };
    const config = configWith([other, latestRule, { name: "added-since-open" }]);
    const before = JSON.stringify(config);
    const patch = buildAlertRulePatch(config, rule, { threshold: 8 });
    expect(patch).toEqual({
      alerting: { rules: [other, { ...latestRule, threshold: 8 }, { name: "added-since-open" }] },
    });
    expect(patch.alerting.rules[0]).toBe(other);
    expect(JSON.stringify(config)).toBe(before);
    expect(patch.alerting).not.toHaveProperty("enabled");
    expect(patch.alerting).not.toHaveProperty("channels");
  });

  it("pauses and resumes through enabled without dropping or recreating any rule", () => {
    const other = { name: "requests", unknown: null };
    const paused = buildAlertRulePatch(configWith([rule, other]), rule, { enabled: false });
    expect(paused.alerting.rules).toEqual([{ ...rule, enabled: false }, other]);
    const resumed = buildAlertRulePatch(
      configWith(paused.alerting.rules), paused.alerting.rules[0], { enabled: true },
    );
    expect(resumed.alerting.rules).toEqual([rule, other]);
  });

  it("removes an explicitly cleared duration but preserves all untouched defaults", () => {
    const original = { ...rule, window: "5m", cooldown: "1h" };
    const patch = buildAlertRulePatch(configWith([original]), original, { cooldown: undefined });
    expect(patch.alerting.rules[0]).toEqual({ ...rule, window: "5m" });
  });

  it("patches only the matching key in a legacy map, including deletion semantics", () => {
    const stored = { threshold: 5, cooldown: "1h", unknown: null };
    const config = configWith({ errors: stored, untouched: { unknown: null } });
    const patch = buildAlertRulePatch(config, { name: "errors", ...stored }, {
      threshold: 7, cooldown: undefined,
    });
    expect(patch).toEqual({ alerting: { rules: { errors: { threshold: 7, cooldown: null } } } });
    expect(config.alerting.rules.errors).toBe(stored);
  });

  it.each([
    ["missing", []],
    ["ambiguous", [rule, { ...rule }]],
    ["changed", [{ ...rule, threshold: 99 }]],
  ])("refuses a %s target rather than overwriting someone else's work", (_reason, rules) => {
    expect(() => buildAlertRulePatch(configWith(rules), rule, { threshold: 8 }))
      .toThrow(/changed|no longer|more than one/i);
  });

  it("allows a concurrent change to an untouched target field and preserves it", () => {
    const latest = { ...rule, channels: ["new-oncall"], enabled: false };
    expect(buildAlertRulePatch(configWith([latest]), rule, { threshold: 8 }).alerting.rules)
      .toEqual([{ ...latest, threshold: 8 }]);
  });

  it("refuses an unreadable rules shape instead of treating it as an empty list", () => {
    expect(() => buildAlertRulePatch({}, rule, { enabled: false })).toThrow(/reload/i);
  });

  it("does not allow a rename or an unknown-field patch", () => {
    expect(() => buildAlertRulePatch(configWith([rule]), rule, { name: "renamed" }))
      .toThrow(/field/i);
    expect(() => buildAlertRulePatch(configWith([rule]), rule, { custom: {} }))
      .toThrow(/field/i);
  });
});
