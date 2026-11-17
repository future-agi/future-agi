import { afterEach, describe, expect, it } from "vitest";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { setLaunchOverrideForTest, surfaceShownIn } from "../launchSwitch";
import { datasetCaps } from "src/sections/develop/realBackendCaps";

// Copying or moving a dataset into another workspace is its own hidden
// surface, split from the dataset switcher: the switcher keeps only its
// picker, and only the copy-and-move claim turns the real Duplicate into the
// workspace copy-and-move dialog. No other claim reaches it, and it reaches
// no other dataset capability.

const MOVE = "dataset.duplicateMove";
const SWITCHER = "dataset.switcher";
const OPTION = "copy:duplicate-move";
const OTHERS = [
  SWITCHER,
  "dataset.settings",
  "list.organize.folders",
  "list.organize.star",
  "list.organize.query",
];
const state = (experimentsBasic, ids = []) => ({
  launch: true,
  experimentsBasic,
  madeReal: new Set(ids),
});
// The switch ships on, so every capability is on (the last block below). The
// cases that pin what a launch build hides read the capabilities with it off
// in the override, so the gates decide as they do with the switch off.
const capsUnder = (current) => {
  setLaunchOverrideForTest({ ...current, showUnreleased: false });
  return datasetCaps(true);
};

afterEach(() => setLaunchOverrideForTest(null));

describe("workspace copy and move scope", () => {
  it("gives copy and move its own hidden ORG surface, split from the switcher", () => {
    expect(SURFACE_BY_ID[MOVE]).toMatchObject({
      ships: "hidden",
      madeRealBy: "ORG",
      options: [OPTION],
    });
    expect(SURFACE_BY_ID[MOVE].parent).toBeUndefined();
    expect(SURFACE_BY_ID[MOVE].experiments).toBeUndefined();
    expect(SURFACE_BY_ID[MOVE].gate).toContain(
      "realBackendCaps.js duplicateMove",
    );
    expect(SURFACE_BY_ID[SWITCHER].options).toEqual(["header:switch-dataset"]);
    // The prototype-only bundle keeps its copy:* glob; the switcher no longer
    // owns the option.
    expect(surfacesOfOption(OPTION).map((s) => s.id)).toEqual([
      "shell.protoOnly",
      MOVE,
    ]);
    expect(laneAdditions).toContainEqual({
      id: MOVE,
      slot: "ORG",
      ships: "hidden",
      splitFrom: SWITCHER,
      options: [OPTION],
    });
  });

  it.each([false, true])(
    "only its own claim turns the real duplicate into copy and move (experimentsBasic=%s)",
    (basic) => {
      const unclaimed = capsUnder(state(basic));
      expect(unclaimed.duplicateMove).toBe(false);
      for (const other of OTHERS) {
        expect(capsUnder(state(basic, [other])).duplicateMove, other).toBe(
          false,
        );
        expect(surfaceShownIn(MOVE, state(basic, [other])), other).toBe(false);
      }
      const claimed = capsUnder(state(basic, [MOVE]));
      expect(claimed.duplicateMove).toBe(true);
      expect(surfaceShownIn(SWITCHER, state(basic, [MOVE]))).toBe(false);
      // The claim changes no other dataset capability.
      for (const [key, value] of Object.entries(unclaimed)) {
        if (key === "duplicateMove") continue;
        expect(claimed[key], key).toEqual(value);
      }
    },
  );

  it("keeps copy and move in the prototype set", () => {
    expect(datasetCaps(false).duplicateMove).toBe(true);
  });
});

// The release as shipped: the switch stays on, so the real Duplicate is copy
// and move with no claim, flag OFF and ON. A gate check only; it checks no
// words.
describe("workspace copy and move scope as shipped (unreleased parts shown)", () => {
  it("turns the real duplicate into copy and move without a claim", () => {
    for (const basic of [false, true]) {
      setLaunchOverrideForTest(state(basic));
      expect(datasetCaps(true).duplicateMove, `basic=${basic}`).toBe(true);
      expect(surfaceShownIn(MOVE, state(basic)), `basic=${basic}`).toBe(true);
    }
  });
});
