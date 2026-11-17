import { afterEach, describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { setLaunchOverrideForTest, surfaceShownIn } from "../launchSwitch";

// The pass threshold set in a dataset's evaluation editor is its own hidden
// surface. The other eval editor and create options stay on the bundle and
// this claim never reveals them.

const BAR = "evals.editorPassThreshold";
const BUNDLE = "evals.extras";
const BAR_OPTIONS = ["editor:pass-threshold"];
const REST = [
  "editor:error-localization",
  "editor:prompt",
  "editor:few-shot",
  "editor:output-type",
  "editor:composite-weights",
  "evalcreate:type",
  "evalcreate:save-add",
  "groups:add-a-group",
  "test:editor",
];
const state = (experimentsBasic, ids = []) => ({
  launch: true,
  experimentsBasic,
  madeReal: new Set(ids),
});
const shown = (option, current) =>
  surfacesOfOption(option).every((s) => surfaceShownIn(s.id, current));

afterEach(() => setLaunchOverrideForTest(null));
// The switch ships on, so every surface shows (the last block below). The
// cases that pin what the launch gates hide turn it off in the override
// first, so the gates decide as they do with the switch off.
const switchOff = () => setLaunchOverrideForTest({ showUnreleased: false });

describe("eval editor pass threshold scope", () => {
  it("gives the editor's pass threshold its own hidden surface", () => {
    expect(SURFACE_BY_ID[BAR]).toMatchObject({
      ships: "hidden",
      madeRealBy: "EVAL",
      options: BAR_OPTIONS,
    });
    expect(SURFACE_BY_ID[BAR].parent).toBeUndefined();
    expect(SURFACE_BY_ID[BAR].experiments).toBeUndefined();
    expect(SURFACE_BY_ID[BUNDLE].options).toEqual(REST);
    expect(surfacesOfOption("editor:pass-threshold").map((s) => s.id)).toEqual([
      BAR,
    ]);
    expect(laneAdditions).toContainEqual({
      id: BAR,
      slot: "EVAL",
      ships: "hidden",
      splitFrom: BUNDLE,
      options: BAR_OPTIONS,
    });
  });

  it("names the binding editor, the saved run setting and the version it keeps", () => {
    const { label, gate, why } = SURFACE_BY_ID[BAR];
    expect(label).toMatch(/pass threshold/i);
    expect(gate).toContain("EvalPickerConfigFull");
    expect(why).toContain("run_config.pass_threshold");
    expect(why).toMatch(/no new version/);
    expect(why).toMatch(/organization and workspace/);
  });

  it.each([false, true])(
    "the pass threshold claim reveals only that option (experimentsBasic=%s)",
    (basic) => {
      switchOff();
      const revealed = Object.keys(inventory.classes).filter(
        (option) =>
          !shown(option, state(basic)) && shown(option, state(basic, [BAR])),
      );
      expect(revealed).toEqual(BAR_OPTIONS);
      for (const option of REST)
        expect(shown(option, state(basic, [BAR]))).toBe(false);
    },
  );

  it.each([false, true])(
    "without the claim the pass threshold option stays hidden (experimentsBasic=%s)",
    (basic) => {
      switchOff();
      for (const option of BAR_OPTIONS)
        expect(shown(option, state(basic))).toBe(false);
    },
  );
});

// The release as shipped: the switch stays on, so the pass threshold option
// and the bundle's options show without a claim, flag OFF and ON. A gate
// check only; it checks no words.
describe("eval editor pass threshold scope as shipped (unreleased parts shown)", () => {
  it("shows the pass threshold option and the bundle's options without a claim", () => {
    for (const basic of [false, true]) {
      const hidden = [...BAR_OPTIONS, ...REST].filter(
        (option) => !shown(option, state(basic)),
      );
      expect(hidden, `experimentsBasic=${basic}`).toEqual([]);
    }
  });
});
