import { describe, it, expect } from "vitest";
import { validateEnvName, MAX_ENV_NAME } from "../renameEnvironment";

describe("validateEnvName (§8)", () => {
  it("rejects a blank or whitespace-only name (not a reset)", () => {
    expect(validateEnvName("")).toMatchObject({ ok: false });
    expect(validateEnvName("   ")).toMatchObject({ ok: false });
    expect(validateEnvName(null)).toMatchObject({ ok: false });
  });

  it("trims and accepts a normal name", () => {
    expect(validateEnvName("  Ride booking  ")).toEqual({
      ok: true,
      value: "Ride booking",
      error: null,
    });
  });

  it("accepts exactly the max length and rejects one over", () => {
    const max = "a".repeat(MAX_ENV_NAME);
    expect(validateEnvName(max).ok).toBe(true);
    expect(validateEnvName(`${max}b`).ok).toBe(false);
  });
});
