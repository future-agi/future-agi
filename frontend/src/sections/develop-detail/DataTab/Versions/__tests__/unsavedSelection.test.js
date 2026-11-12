import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  doorSplitOf,
  pendingBySplit,
  remapRowIds,
  unsavedSelection,
  unsavedSentence,
} from "../unsavedSelection";

const add = (id, split_ids = []) => ({
  op: "add_row",
  temp_id: id,
  cells: {},
  split_ids,
});
const ops = [add("imp-1", ["S9"]), add("imp-2", ["S9"]), add("new-1")];

afterEach(() => setLaunchOverrideForTest(null));

describe("unsaved selection (IT-R1 G5 P1)", () => {
  beforeEach(() => setLaunchOverrideForTest({ launch: false }));
  it("counts the door rows that only exist as pending changes", () => {
    expect(unsavedSelection({ ops, rowIds: ["imp-1", "imp-2"] })).toEqual({
      total: 2,
      unsaved: 2,
    });
    expect(unsavedSelection({ ops, rowIds: ["imp-1", "ROW_12"] })).toEqual({
      total: 2,
      unsaved: 1,
    });
    expect(unsavedSelection({ ops, rowIds: [] })).toEqual({
      total: 0,
      unsaved: 0,
    });
  });

  it("counts pending rows headed for a chosen split", () => {
    const splits = [
      { id: "S9", row_count: 0 },
      { id: "S1", row_count: 22 },
    ];
    expect(unsavedSelection({ ops, splitIds: ["S9"], splits })).toEqual({
      total: 2,
      unsaved: 2,
    });
    expect(unsavedSelection({ ops, splitIds: ["S1"], splits })).toEqual({
      total: 22,
      unsaved: 0,
    });
  });

  it("says one sentence, the same the server says", () => {
    expect(unsavedSentence({ total: 6, unsaved: 6 }, 6)).toBe(
      "None of the 6 selected rows are in v6. Save them as a new version first.",
    );
    expect(unsavedSentence({ total: 8, unsaved: 2 }, 6)).toBe(
      "2 of the 8 selected rows aren't in v6 yet. Save them as a new version first, or they are skipped.",
    );
    expect(unsavedSentence({ total: 3, unsaved: 0 }, 6)).toBeNull();
  });

  it("finds the one split the door rows go to", () => {
    expect(doorSplitOf(ops, ["imp-1", "imp-2"])).toBe("S9");
    expect(doorSplitOf(ops, ["imp-1", "new-1"])).toBeNull();
    expect(pendingBySplit(ops)).toEqual({ S9: 2 });
  });

  it("follows temp ids into the saved version", () => {
    expect(remapRowIds(["imp-1", "ROW_3"], { "imp-1": "ROW_249" })).toEqual([
      "ROW_249",
      "ROW_3",
    ]);
  });
});

// RED on the pinned base: the sentence helper ignores the launch surface.
describe("unsaved selection copy with dataset.versions hidden", () => {
  beforeEach(() =>
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: [],
    }),
  );

  it.each([
    { total: 1, unsaved: 1 },
    { total: 6, unsaved: 6 },
    { total: 8, unsaved: 2 },
    { total: 3, unsaved: 0 },
  ])("does not offer save wording for %j", (info) => {
    const sentence = unsavedSentence(info, 6);
    expect(sentence).toBeNull();
    expect(sentence ?? "").not.toMatch(/\bv\d+\b|version/i);
  });

  it("leaves selection accounting and ids intact while hiding only copy", () => {
    const rowIds = ["imp-1", "ROW_12"];
    const info = unsavedSelection({ ops, rowIds });
    expect(info).toEqual({ total: 2, unsaved: 1 });
    expect(unsavedSentence(info, 6)).toBeNull();
    expect(rowIds).toEqual(["imp-1", "ROW_12"]);
    expect(ops).toEqual([
      add("imp-1", ["S9"]),
      add("imp-2", ["S9"]),
      add("new-1"),
    ]);
  });
});
