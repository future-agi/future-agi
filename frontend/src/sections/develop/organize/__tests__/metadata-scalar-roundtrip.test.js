import { describe, expect, it } from "vitest";
import { fullPatch, mapFromRows, rowsOf, typedValue, valueInput } from "../metadataModel";

const inputRows = (metadata) => rowsOf(metadata).map(({ key, value }) => ({ key, value: valueInput(value) }));
const mergePatch = (current, patch) => {
  const next = { ...current };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete next[key]; else next[key] = value;
  }
  return next;
};

describe("saved metadata scalars survive reopening row mode", () => {
  it.each([
    1e-7, 1e21, -1e-7, 0, false, true, "2", " true ", "false ",
    '"literal quotes"', "'single quotes'", '"a\\nb"', "1e-7", "ordinary text",
  ])("round-trips %j without changing its type or contents", (value) => {
    expect(typedValue(valueInput(value))).toEqual(value);
  });

  it("saves an unchanged reopened map with its exact scalar values", () => {
    const before = {
      number: 1e-7,
      boolean: false,
      spaced: " true ",
      quotes: '"literal quotes"',
      numeric_text: "1e21",
    };
    const rows = rowsOf(before).map(({ key, value }) => ({ key, value: valueInput(value) }));
    const parsed = mapFromRows(rows);
    expect(parsed.error).toBeNull();
    expect(parsed.metadata).toEqual(before);
    expect(fullPatch(before, parsed.metadata)).toEqual({});
  });

  it.each([
    ["LF", "\n"], ["CR", "\r"], ["CRLF", "\r\n"],
  ])("quotes persisted %s with quotes and backslashes for a single-line input", (_name, separator) => {
    const value = `first${separator}"quoted"\\path${separator}last`;
    const encoded = valueInput(value);
    expect(encoded).toBe(JSON.stringify(value));
    expect(encoded).not.toMatch(/[\r\n]/);
    expect(typeof typedValue(encoded)).toBe("string");
    expect(typedValue(encoded)).toBe(value);
    const before = { note: value, code: "2", score: 2, ready: false };
    const parsed = mapFromRows(inputRows(before));
    expect(parsed.error).toBeNull();
    expect(parsed.metadata).toEqual(before);
    const patch = fullPatch(before, parsed.metadata);
    expect(patch).toEqual({});
    const reopened = mapFromRows(inputRows(mergePatch(before, patch)));
    expect(reopened.error).toBeNull();
    expect(reopened.metadata).toEqual(before);
    expect(fullPatch(before, reopened.metadata)).toEqual({});
  });
});

describe("single-dataset patches express only edits to the opening snapshot", () => {
  it("merges independent editors without clobbering a changed owner or resurrecting an unedited key", () => {
    const before = { owner: "old", score: 1, retired: "old", ready: false };
    const editorA = mapFromRows(inputRows(before).map((row) => row.key === "owner" ? { ...row, value: "new" } : row));
    const editorB = mapFromRows(inputRows(before).map((row) => row.key === "score" ? { ...row, value: "2" } : row));
    expect(editorA.error).toBeNull();
    expect(editorB.error).toBeNull();
    expect(editorA.metadata).toEqual({ ...before, owner: "new" });
    expect(editorB.metadata).toEqual({ ...before, score: 2 });
    const patchA = fullPatch(before, editorA.metadata);
    const patchB = fullPatch(before, editorB.metadata);
    expect(patchA).toEqual({ owner: "new" });
    expect(patchB).toEqual({ score: 2 });
    const current = mergePatch(mergePatch(before, patchA), { retired: null, concurrent: true });
    const expected = { owner: "new", score: 2, ready: false, concurrent: true };
    expect(mergePatch(current, patchB)).toEqual(expected);
    const unchanged = mapFromRows(inputRows(before));
    expect(unchanged.error).toBeNull();
    expect(unchanged.metadata).toEqual(before);
    expect(fullPatch(before, unchanged.metadata)).toEqual({});
    expect(mergePatch(expected, fullPatch(before, unchanged.metadata))).toEqual(expected);
    expect(before).toEqual({ owner: "old", score: 1, retired: "old", ready: false });
  });

  it("includes additions, scalar type changes and explicit deletes but omits equal scalars", () => {
    const before = { code: "2", ready: false, score: 0, keep: "", remove: "old" };
    const rows = inputRows(before)
      .filter((row) => row.key !== "remove")
      .map((row) => row.key === "code" ? { ...row, value: "2" } : row);
    rows.push({ key: "added", value: "false" });
    const parsed = mapFromRows(rows);
    expect(parsed.error).toBeNull();
    expect(parsed.metadata).toEqual({ code: 2, ready: false, score: 0, keep: "", added: false });
    const patch = fullPatch(before, parsed.metadata);
    expect(patch).toEqual({ code: 2, added: false, remove: null });
    expect(mergePatch({ ...before, keep: "concurrent", unrelated: true }, patch))
      .toEqual({ code: 2, ready: false, score: 0, keep: "concurrent", added: false, unrelated: true });
  });
});
