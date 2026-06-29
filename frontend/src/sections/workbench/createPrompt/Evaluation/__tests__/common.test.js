import { describe, it, expect } from "vitest";
import { isUnsavedRow, CELL_STATE } from "../common";

const E = CELL_STATE.EMPTY;

describe("isUnsavedRow", () => {
  it("is true for a locally-added row (flagged _isLocal)", () => {
    expect(
      isUnsavedRow({ id: "1", _isLocal: true, TOPIC: E, "Output-v2": E }),
    ).toBe(true);
  });

  it("stays true after the row is run/compared — keys off the flag, not cell contents", () => {
    expect(
      isUnsavedRow({
        id: "1",
        _isLocal: true,
        TOPIC: "unicorn",
        "Output-v2": "a story",
      }),
    ).toBe(true);
  });

  it("is false for a row with empty cells but no _isLocal flag (the old EMPTY-sniffing misfire)", () => {
    expect(isUnsavedRow({ id: "0", TOPIC: E, "Output-v2": E })).toBe(false);
  });

  it("is false for a persisted row with content", () => {
    expect(
      isUnsavedRow({ id: "0", TOPIC: "UNICORN", "Output-v2": "a story" }),
    ).toBe(false);
  });

  it("returns false for nullish input", () => {
    expect(isUnsavedRow(null)).toBe(false);
    expect(isUnsavedRow(undefined)).toBe(false);
  });
});
