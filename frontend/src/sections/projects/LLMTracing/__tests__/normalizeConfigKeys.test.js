import { describe, it, expect } from "vitest";
import { normalizeConfigKeys } from "../common";

describe("normalizeConfigKeys", () => {
  it("camelCases snake_case keys so is_visible resolves as isVisible", () => {
    const out = normalizeConfigKeys([{ id: "trace_id", is_visible: true }]);
    expect(out[0].isVisible).toBe(true);
  });

  it("preserves id values and converts the rest of the keys", () => {
    expect(
      normalizeConfigKeys([
        { id: "x", output_type: "score", is_visible: false },
      ]),
    ).toEqual([{ id: "x", outputType: "score", isVisible: false }]);
  });

  it("returns undefined for a missing config", () => {
    expect(normalizeConfigKeys(undefined)).toBeUndefined();
  });
});
