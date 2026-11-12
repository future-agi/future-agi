import { describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { MADE_REAL } from "../madeReal";
import { surfaceShownIn } from "../launchSwitch";

// The dataset eval list's version notice is the drawer's own option. The New
// experiment wizard's eval card chips are a separate hidden surface, so a
// claim on the notice never shows the chips.

const NOTICE = "dataset.evalVersionNotice";
const CHIPS = "exp.new.evalCardChips";
const DRAWER_OPTION = "ver:saved-list-notice";
const launch = (experimentsBasic, ids) => ({
  launch: true,
  experimentsBasic,
  madeReal: new Set(ids),
});
const optionHidden = (option, state) =>
  surfacesOfOption(option).some((s) => !surfaceShownIn(s.id, state));
const hiddenOptions = (state) =>
  new Set(Object.keys(inventory.classes).filter((o) => optionHidden(o, state)));

describe("eval version notice scope", () => {
  it("maps the notice surface to the drawer list option only", () => {
    expect(SURFACE_BY_ID[NOTICE].options).toEqual([DRAWER_OPTION]);
    const hosts = surfacesOfOption(DRAWER_OPTION).map((s) => s.id);
    expect(hosts).toContain(NOTICE);
    expect(hosts).not.toContain(CHIPS);
  });

  it("puts the wizard card chips under their own hidden wizard surface", () => {
    expect(SURFACE_BY_ID[CHIPS]).toMatchObject({
      ships: "hidden",
      parent: "exp.new",
      experiments: true,
      madeRealBy: "EVAL",
      options: ["wiz:card-chips"],
    });
    expect(
      surfacesOfOption("wiz:card-chips")
        .map((s) => s.id)
        .sort(),
    ).toEqual(["exp.new", CHIPS]);
  });

  it.each([false, true])(
    "a notice claim reveals no inventory option beyond the drawer notice (experimentsBasic=%s)",
    (basic) => {
      const before = hiddenOptions(launch(basic, []));
      const after = hiddenOptions(launch(basic, [NOTICE]));
      const revealed = [...before].filter(
        (o) => !after.has(o) && o !== DRAWER_OPTION,
      );
      expect(revealed).toEqual([]);
      expect(optionHidden("wiz:card-chips", launch(basic, [NOTICE]))).toBe(
        true,
      );
    },
  );

  it.each([false, true])(
    "a notice claim shows the notice and keeps the chips hidden (experimentsBasic=%s)",
    (basic) => {
      const state = launch(basic, [NOTICE]);
      expect(surfaceShownIn(NOTICE, state)).toBe(true);
      expect(surfaceShownIn(CHIPS, state)).toBe(false);
    },
  );

  it("shows the chips only with their own claim and experimentsBasic", () => {
    expect(surfaceShownIn(CHIPS, launch(true, [CHIPS]))).toBe(true);
    expect(surfaceShownIn(CHIPS, launch(false, [CHIPS]))).toBe(false);
    expect(surfaceShownIn(NOTICE, launch(true, [CHIPS]))).toBe(false);
    expect(surfaceShownIn(CHIPS, { launch: false })).toBe(true);
  });

  it("keeps the dark notice and the chips unclaimed in every slot", () => {
    expect(Object.values(MADE_REAL).flat()).not.toContain(NOTICE);
    expect(Object.values(MADE_REAL).flat()).not.toContain(CHIPS);
  });
});
