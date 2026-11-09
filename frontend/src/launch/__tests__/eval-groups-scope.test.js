import { describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { surfaceShownIn } from "../launchSwitch";

// Creating an eval group and managing its members on the Groups pages is its
// own hidden surface. The rest of the eval editor and create options stay on
// the bundle: a groups claim never reveals them, and adding a group to an
// experiment is not part of this surface.

const GROUPS = "evals.groups";
const BUNDLE = "evals.extras";
const GROUP_OPTIONS = ["groups:create", "groups:detail"];
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

describe("eval groups scope", () => {
  it("gives group create and group detail their own hidden surface", () => {
    expect(SURFACE_BY_ID[GROUPS]).toMatchObject({
      ships: "hidden",
      madeRealBy: "EVAL",
      options: GROUP_OPTIONS,
    });
    expect(SURFACE_BY_ID[GROUPS].parent).toBeUndefined();
    expect(SURFACE_BY_ID[GROUPS].experiments).toBeUndefined();
    expect(SURFACE_BY_ID[BUNDLE].options).toEqual(REST);
    for (const option of GROUP_OPTIONS)
      expect(surfacesOfOption(option).map((s) => s.id)).toEqual([GROUPS]);
    expect(laneAdditions).toContainEqual({
      id: GROUPS,
      slot: "EVAL",
      ships: "hidden",
      splitFrom: BUNDLE,
      options: GROUP_OPTIONS,
    });
  });

  it("describes the Groups pages only; adding a group elsewhere stays on the bundle", () => {
    const { label, gate, why } = SURFACE_BY_ID[GROUPS];
    expect(label).toMatch(/\bgroups?\b/i);
    expect(gate).toContain("sections/evals/Groups");
    expect(why).toMatch(/organization and workspace/);
    expect(why).toContain("groups:add-a-group");
    expect(surfacesOfOption("groups:add-a-group").map((s) => s.id)).toEqual([
      BUNDLE,
    ]);
  });

  it.each([false, true])(
    "the groups claim reveals only group create and detail (experimentsBasic=%s)",
    (basic) => {
      const revealed = Object.keys(inventory.classes).filter(
        (option) =>
          !shown(option, state(basic)) && shown(option, state(basic, [GROUPS])),
      );
      expect(revealed).toEqual(GROUP_OPTIONS);
      for (const option of REST)
        expect(shown(option, state(basic, [GROUPS]))).toBe(false);
    },
  );

  it.each([false, true])(
    "without the claim both group options stay hidden (experimentsBasic=%s)",
    (basic) => {
      for (const option of GROUP_OPTIONS)
        expect(shown(option, state(basic))).toBe(false);
    },
  );
});
