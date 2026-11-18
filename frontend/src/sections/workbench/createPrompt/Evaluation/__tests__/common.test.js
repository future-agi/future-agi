import { describe, it, expect } from "vitest";
import { isUnsavedRow, mergeUnsavedRows, CELL_STATE } from "../common";

const E = CELL_STATE.EMPTY;

const backendRow = (id) => ({ id, TOPIC: "backend", "Output-v2": "out" });
const localRow = (id) => ({ id, _isLocal: true, TOPIC: E, "Output-v2": E });

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

describe("mergeUnsavedRows", () => {
  it("Scenario 1 — added but unrun row survives an eval refetch", () => {
    // Backend has rows 0-4; user added a local row "5" and hasn't run it.
    const next = [0, 1, 2, 3, 4].map((i) => backendRow(`${i}`));
    const prev = [...next, localRow("5")];

    const merged = mergeUnsavedRows(next, prev);

    expect(merged).toHaveLength(6);
    expect(merged.at(-1)).toBe(prev.at(-1)); // same object, stable row identity
  });

  it("Scenario 2 — a run row that now persists in the backend isn't duplicated", () => {
    // Local row "5" was run; the refetch now returns it as a backend row.
    const next = [0, 1, 2, 3, 4, 5].map((i) => backendRow(`${i}`));
    const prev = [
      ...[0, 1, 2, 3, 4].map((i) => backendRow(`${i}`)),
      localRow("5"),
    ];

    const merged = mergeUnsavedRows(next, prev);

    expect(merged).toBe(next);
    expect(merged.filter((r) => r.id === "5")).toHaveLength(1);
  });

  it("Scenario 3 — no unsaved rows: returns backend rows unchanged", () => {
    const next = [0, 1, 2].map((i) => backendRow(`${i}`));
    const prev = [0, 1, 2].map((i) => backendRow(`${i}`));

    expect(mergeUnsavedRows(next, prev)).toBe(next);
  });

  it("partial persistence — first added row persists, second stays local", () => {
    // Added "5" and "6"; only "5" was run, so the backend now returns 0-5.
    const next = [0, 1, 2, 3, 4, 5].map((i) => backendRow(`${i}`));
    const prev = [
      ...[0, 1, 2, 3, 4].map((i) => backendRow(`${i}`)),
      localRow("5"),
      localRow("6"),
    ];

    const merged = mergeUnsavedRows(next, prev);

    expect(merged.map((r) => r.id)).toEqual([
      "0",
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
    ]);
    expect(merged.at(-1)._isLocal).toBe(true);
  });

  it("returns next untouched when it holds id-less placeholder rows", () => {
    // processRowData(undefined) returns 6 id-less placeholders; don't graft
    // locals onto a grid whose rows can't be keyed.
    const next = Array.from({ length: 6 }, () => ({
      Variables: "",
      Outputs: "",
    }));
    const prev = [backendRow("0"), localRow("1")];

    expect(mergeUnsavedRows(next, prev)).toBe(next);
  });

  it("returns next for non-array or empty prev", () => {
    const next = [backendRow("0")];
    expect(mergeUnsavedRows(next, [])).toBe(next);
    expect(mergeUnsavedRows(next, null)).toBe(next);
    expect(mergeUnsavedRows(null, [localRow("0")])).toBe(null);
  });

  it("keeps a sparse-id local row when the backend count grows for another reason", () => {
    // Local id "7" (from add-then-delete churn) must not be dropped just
    // because the backend grew to 6 rows — its id isn't among them.
    const next = [0, 1, 2, 3, 4, 5].map((i) => backendRow(`${i}`));
    const prev = [
      ...[0, 1, 2, 3, 4].map((i) => backendRow(`${i}`)),
      localRow("7"),
    ];

    const merged = mergeUnsavedRows(next, prev);

    expect(merged.map((r) => r.id)).toContain("7");
    expect(merged).toHaveLength(7);
  });
});
