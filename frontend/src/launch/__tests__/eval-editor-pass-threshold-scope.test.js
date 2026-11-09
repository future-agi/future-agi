import { describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { surfaceShownIn } from "../launchSwitch";

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
      for (const option of BAR_OPTIONS)
        expect(shown(option, state(basic))).toBe(false);
    },
  );
});
