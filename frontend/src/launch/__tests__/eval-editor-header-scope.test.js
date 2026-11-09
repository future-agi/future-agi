import { describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { surfaceShownIn } from "../launchSwitch";

// The evaluation editor's header, naming the saved version being configured,
// is its own hidden surface. The other eval editor and create options stay
// on the bundle and this claim never reveals them.

const HEADER = "evals.editorHeader";
const BUNDLE = "evals.extras";
const HEADER_OPTIONS = ["editor:header"];
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

describe("eval editor header scope", () => {
  it("gives the editor header its own hidden surface", () => {
    expect(SURFACE_BY_ID[HEADER]).toMatchObject({
      ships: "hidden",
      madeRealBy: "EVAL",
      options: HEADER_OPTIONS,
    });
    expect(SURFACE_BY_ID[HEADER].parent).toBeUndefined();
    expect(SURFACE_BY_ID[HEADER].experiments).toBeUndefined();
    expect(SURFACE_BY_ID[BUNDLE].options).toEqual(REST);
    expect(surfacesOfOption("editor:header").map((s) => s.id)).toEqual([
      HEADER,
    ]);
    expect(laneAdditions).toContainEqual({
      id: HEADER,
      slot: "EVAL",
      ships: "hidden",
      splitFrom: BUNDLE,
      options: HEADER_OPTIONS,
    });
  });

  it("names the editor header, the version it shows and unsaved edits", () => {
    const { label, gate, why } = SURFACE_BY_ID[HEADER];
    expect(label).toMatch(/header/i);
    expect(gate).toContain("EvalPickerConfigFull");
    expect(gate).toContain("VersionBadge");
    expect(why).toMatch(/current version/);
    expect(why).toMatch(/Edited/);
  });

  it.each([false, true])(
    "the header claim reveals only the editor header (experimentsBasic=%s)",
    (basic) => {
      const revealed = Object.keys(inventory.classes).filter(
        (option) =>
          !shown(option, state(basic)) && shown(option, state(basic, [HEADER])),
      );
      expect(revealed).toEqual(HEADER_OPTIONS);
      for (const option of REST)
        expect(shown(option, state(basic, [HEADER]))).toBe(false);
    },
  );

  it.each([false, true])(
    "without the claim the editor header stays hidden (experimentsBasic=%s)",
    (basic) => {
      for (const option of HEADER_OPTIONS)
        expect(shown(option, state(basic))).toBe(false);
    },
  );
});
