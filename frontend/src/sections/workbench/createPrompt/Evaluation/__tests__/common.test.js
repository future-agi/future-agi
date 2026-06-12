import { describe, it, expect } from "vitest";
import { isUnsavedRow, CELL_STATE } from "../common";

const E = CELL_STATE.EMPTY;

describe("isUnsavedRow", () => {
  it("is true for a freshly added row where every cell is empty", () => {
    expect(isUnsavedRow({ id: "1", TOPIC: E, "Output-v2": E })).toBe(true);
  });

  it("stays true when a variable is filled but the output is still empty", () => {
    // Typing a variable does not persist the row — it's still local.
    expect(isUnsavedRow({ id: "1", TOPIC: "unicorn", "Output-v2": E })).toBe(
      true,
    );
  });

  it("is false for a persisted row with real content", () => {
    expect(
      isUnsavedRow({ id: "0", TOPIC: "UNICORN", "Output-v2": "a story" }),
    ).toBe(false);
  });

  it('treats backend empties ("") as persisted, not unsaved', () => {
    expect(isUnsavedRow({ id: "0", TOPIC: "", "Output-v2": "" })).toBe(false);
  });

  it("ignores the id key", () => {
    expect(isUnsavedRow({ id: "1" })).toBe(false);
  });

  it("is false while running (LOADING is not the EMPTY sentinel)", () => {
    expect(
      isUnsavedRow({ id: "1", TOPIC: "unicorn", "Output-v2": CELL_STATE.LOADING }),
    ).toBe(false);
  });

  it("returns false for nullish input", () => {
    expect(isUnsavedRow(null)).toBe(false);
    expect(isUnsavedRow(undefined)).toBe(false);
  });
});
