import { describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { surfaceShownIn } from "../launchSwitch";

const TABLE = "exp.new.savedInputReads";
const CARD = "exp.new.readsTable";
const state = (experimentsBasic, ids = []) => ({
  launch: true,
  experimentsBasic,
  madeReal: new Set(ids),
});
const shown = (option, current) =>
  surfacesOfOption(option).every((s) => surfaceShownIn(s.id, current));

describe("saved evaluation input preview scope", () => {
  it("separates the saved-input table from the mapping card", () => {
    expect(SURFACE_BY_ID[TABLE]).toMatchObject({
      ships: "hidden",
      parent: "exp.new",
      experiments: true,
      madeRealBy: "EVAL",
      options: ["wiz:reads-table"],
    });
    expect(SURFACE_BY_ID[CARD].options).toEqual(["wiz:card-mapping-preview"]);
  });

  it.each([false, true])(
    "the table claim reveals only its own option (experimentsBasic=%s)",
    (basic) => {
      const revealed = Object.keys(inventory.classes).filter(
        (option) =>
          !shown(option, state(basic)) && shown(option, state(basic, [TABLE])),
      );
      expect(revealed).toEqual(basic ? ["wiz:reads-table"] : []);
      expect(shown("wiz:card-mapping-preview", state(basic, [TABLE]))).toBe(
        false,
      );
    },
  );

  it("requires its own claim and the experiment host", () => {
    expect(surfaceShownIn(TABLE, state(true))).toBe(false);
    expect(surfaceShownIn(TABLE, state(false, [TABLE]))).toBe(false);
    expect(surfaceShownIn(TABLE, state(true, [CARD]))).toBe(false);
    expect(surfaceShownIn(TABLE, state(true, [TABLE]))).toBe(true);
  });
});
