import { describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { surfaceShownIn } from "../launchSwitch";

// The output type and pass threshold chosen when creating an evaluation, on
// both create forms, are their own hidden surface. The other eval editor and
// create options stay on the bundle and this claim never reveals them.

const OUTPUT = "evals.createOutputType";
const BUNDLE = "evals.extras";
const OUTPUT_OPTIONS = ["evalcreate:output-type"];
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

describe("eval create output type scope", () => {
  it("gives the create output type its own hidden surface", () => {
    expect(SURFACE_BY_ID[OUTPUT]).toMatchObject({
      ships: "hidden",
      madeRealBy: "EVAL",
      options: OUTPUT_OPTIONS,
    });
    expect(SURFACE_BY_ID[OUTPUT].parent).toBeUndefined();
    expect(SURFACE_BY_ID[OUTPUT].experiments).toBeUndefined();
    expect(SURFACE_BY_ID[BUNDLE].options).toEqual(REST);
    expect(surfacesOfOption("evalcreate:output-type").map((s) => s.id)).toEqual(
      [OUTPUT],
    );
    expect(laneAdditions).toContainEqual({
      id: OUTPUT,
      slot: "EVAL",
      ships: "hidden",
      splitFrom: BUNDLE,
      options: OUTPUT_OPTIONS,
    });
  });

  it("names both create forms, the offered types and the judged cells", () => {
    const { label, gate, why } = SURFACE_BY_ID[OUTPUT];
    expect(label).toMatch(/output type/i);
    expect(gate).toContain("EvalCreatePage");
    expect(gate).toContain("EvalPickerCreateNew");
    expect(gate).toContain("OutputTypeConfig");
    expect(why).toMatch(/Pass\/fail/);
    expect(why).toMatch(/Scoring/);
    expect(why).toMatch(/pass threshold/);
    expect(gate).toContain("OutputTypeConfig on the Create page");
    expect(why).toContain("Create page offers Pass/fail, Scoring and Choices");
    expect(why).toContain("picker's Create new offers Pass/fail and Scoring");
    expect(why).toContain("Choices is not offered in the picker");
  });

  it.each([false, true])(
    "the output type claim reveals only the create output type option (experimentsBasic=%s)",
    (basic) => {
      const revealed = Object.keys(inventory.classes).filter(
        (option) =>
          !shown(option, state(basic)) && shown(option, state(basic, [OUTPUT])),
      );
      expect(revealed).toEqual(OUTPUT_OPTIONS);
      for (const option of REST)
        expect(shown(option, state(basic, [OUTPUT]))).toBe(false);
    },
  );

  it.each([false, true])(
    "without the claim the create output type option stays hidden (experimentsBasic=%s)",
    (basic) => {
      for (const option of OUTPUT_OPTIONS)
        expect(shown(option, state(basic))).toBe(false);
    },
  );
});
