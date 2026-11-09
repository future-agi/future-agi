import { describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { surfaceShownIn } from "../launchSwitch";

// A refused evaluation name, said at the Name field of both create forms, is
// its own hidden surface. The other eval editor and create options stay on
// the bundle and a name claim never reveals them.

const NAME = "evals.createName";
const BUNDLE = "evals.extras";
const NAME_OPTIONS = ["evalcreate:name"];
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

describe("eval create name scope", () => {
  it("gives the create-name refusal its own hidden surface", () => {
    expect(SURFACE_BY_ID[NAME]).toMatchObject({
      ships: "hidden",
      madeRealBy: "EVAL",
      options: NAME_OPTIONS,
    });
    expect(SURFACE_BY_ID[NAME].parent).toBeUndefined();
    expect(SURFACE_BY_ID[NAME].experiments).toBeUndefined();
    expect(SURFACE_BY_ID[BUNDLE].options).toEqual(REST);
    expect(surfacesOfOption("evalcreate:name").map((s) => s.id)).toEqual([
      NAME,
    ]);
    expect(laneAdditions).toContainEqual({
      id: NAME,
      slot: "EVAL",
      ships: "hidden",
      splitFrom: BUNDLE,
      options: NAME_OPTIONS,
    });
  });

  it("names both create forms and the publish rules", () => {
    const { label, gate, why } = SURFACE_BY_ID[NAME];
    expect(label).toMatch(/\bname\b/i);
    expect(gate).toContain("EvalCreatePage");
    expect(gate).toContain("EvalPickerCreateNew");
    expect(why).toMatch(/organization/);
    expect(why).toContain("eval-templates/{id}/update/");
  });

  it.each([false, true])(
    "the name claim reveals only the create-name option (experimentsBasic=%s)",
    (basic) => {
      const revealed = Object.keys(inventory.classes).filter(
        (option) =>
          !shown(option, state(basic)) && shown(option, state(basic, [NAME])),
      );
      expect(revealed).toEqual(NAME_OPTIONS);
      for (const option of REST)
        expect(shown(option, state(basic, [NAME]))).toBe(false);
    },
  );

  it.each([false, true])(
    "without the claim the create-name option stays hidden (experimentsBasic=%s)",
    (basic) => {
      for (const option of NAME_OPTIONS)
        expect(shown(option, state(basic))).toBe(false);
    },
  );
});
