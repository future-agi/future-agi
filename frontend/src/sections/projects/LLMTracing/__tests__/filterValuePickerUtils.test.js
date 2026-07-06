import { describe, it, expect } from "vitest";
import { usesFreeTextValue } from "../filterValuePickerUtils";

describe("usesFreeTextValue — text vs values-picker decision", () => {
  it("text fields always use the free-text input", () => {
    expect(usesFreeTextValue("text", "traces")).toBe(true);
    expect(usesFreeTextValue("text", "dataset")).toBe(true);
  });

  it("string fields use the picker in Observe sources", () => {
    for (const source of ["traces", "sessions", "users"]) {
      expect(usesFreeTextValue("string", source)).toBe(false);
    }
  });

  it("string fields stay free text in non-Observe sources", () => {
    for (const source of ["dataset", "simulation", "experiment"]) {
      expect(usesFreeTextValue("string", source)).toBe(true);
    }
  });
});
