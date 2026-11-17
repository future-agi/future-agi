import { describe, expect, it } from "vitest";
import { previewThresholdValues } from "../components/validation";

// Historical Preview must send thresholds the way Save does: the form resolver
// turns a blank level into "not sent" and a typed level into a number.
describe("previewThresholdValues", () => {
  it("leaves out a blank warning, as Save does for a monitor saved without one", () => {
    const values = { name: "m", critical_threshold_value: "5", warning_threshold_value: "" };
    const out = previewThresholdValues(values);
    expect(out.critical_threshold_value).toBe(5);
    expect(out.warning_threshold_value).toBeUndefined();
    expect(JSON.parse(JSON.stringify(out))).not.toHaveProperty("warning_threshold_value");
    expect(out.name).toBe("m");
  });

  it("leaves out an empty level kept by its saved threshold words", () => {
    const values = {
      critical_threshold_value: "",
      critical_threshold_label: "infinity",
      warning_threshold_value: null,
    };
    const out = previewThresholdValues(values);
    expect(out.critical_threshold_value).toBeUndefined();
    expect(out.warning_threshold_value).toBeUndefined();
    expect(out.critical_threshold_label).toBe("infinity");
  });

  it("keeps numbers and zero", () => {
    const out = previewThresholdValues({ critical_threshold_value: 0, warning_threshold_value: "0.5" });
    expect(out.critical_threshold_value).toBe(0);
    expect(out.warning_threshold_value).toBe(0.5);
  });
});
