import { describe, expect, it } from "vitest";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import {
  FIELD_NAMES,
  PRESETS,
  deciding,
  escalateAt,
  matchPreset,
  presetValues,
  recoverAt,
  simulate,
  toPayload,
  validate,
} from "../escalationSettings";

// Mirrors futureagi/tracer/services/feed_escalation_settings.py RANGES.
const ranges = {
  min_findings: { min: 2, max: 1000000, step: 1 },
  entry_multiplier: { min: 1.1, max: 20, step: 0.1 },
  entry_delta: { min: 1, max: 1000000, step: 1 },
  entry_confirmation_minutes: { min: 5, max: 60, step: 5 },
  recovery_min: { min: 0, max: 999999, step: 1 },
  recovery_multiplier: { min: 1, max: 19.9, step: 0.1 },
  recovery_delta: { min: 0, max: 999999, step: 1 },
  recovery_confirmation_minutes: { min: 5, max: 60, step: 5 },
};
const defaults = {
  min_findings: 20,
  entry_multiplier: "3.0",
  entry_delta: 10,
  entry_confirmation_minutes: 15,
  recovery_min: 5,
  recovery_multiplier: "1.5",
  recovery_delta: 3,
  recovery_confirmation_minutes: 10,
};

describe("escalation presets", () => {
  it.each(PRESETS.map((preset) => [preset.key, preset]))(
    "%s sets all eight values and passes the backend checks",
    (_key, preset) => {
      const values = presetValues(preset, defaults);
      expect(Object.keys(values)).toEqual(FIELD_NAMES);
      expect(validate(values, ranges)).toEqual({});
      expect(matchPreset(values, defaults)).toBe(preset.key);
    },
  );

  it("builds a save body the API contract accepts", () => {
    const body = {
      ...toPayload(presetValues(PRESETS[0], defaults)),
      revision: 7,
    };
    expect(body.recovery_multiplier).toBe("1.3");
    expect(
      validateContractedRequestConfig({
        url: "/tracer/feed/escalation-settings/",
        method: "patch",
        data: body,
      }),
    ).toMatchObject({ ok: true });
  });

  it("treats any other combination as custom", () => {
    const values = presetValues(PRESETS[1], defaults);
    expect(matchPreset({ ...values, entry_delta: 11 }, defaults)).toBeNull();
  });
});

describe("escalation limits", () => {
  const balanced = presetValues(PRESETS[1], defaults);

  it("uses the highest limit, like Policy.thresholds", () => {
    expect(escalateAt(balanced, 10)).toBe(30);
    expect(recoverAt(balanced, 10)).toBe(15);
    expect(deciding(balanced, 10)).toEqual([
      "entry_multiplier",
      "recovery_multiplier",
    ]);
    expect(escalateAt(balanced, 2)).toBe(20);
    expect(deciding(balanced, 2)[0]).toBe("min_findings");
    // 1.5 × 11 = 16.5: 16 recovers, 17 does not.
    expect(recoverAt(balanced, 11)).toBe(16);
  });

  it("treats a blank value as invalid instead of zero", () => {
    expect(validate({ ...balanced, recovery_min: NaN }, ranges)).toHaveProperty(
      "recovery_min",
    );
  });

  it("escalates the sample issue once and recovers after the hold", () => {
    const [span, ...rest] = simulate(balanced, 10);
    expect(rest).toHaveLength(0);
    expect(span.start - span.holdFrom).toBe(15);
    expect(span.end - span.recoverFrom).toBe(10);
    const calm = presetValues(PRESETS[2], defaults);
    expect(simulate({ ...calm, min_findings: 500 }, 10)).toEqual([]);
  });
});
