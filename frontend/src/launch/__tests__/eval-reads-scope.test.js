import { afterEach, describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { setLaunchOverrideForTest, surfaceShownIn } from "../launchSwitch";

const TABLE = "exp.new.savedInputReads";
const CARD = "exp.new.readsTable";
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
      switchOff();
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
    switchOff();
    expect(surfaceShownIn(TABLE, state(true))).toBe(false);
    expect(surfaceShownIn(TABLE, state(false, [TABLE]))).toBe(false);
    expect(surfaceShownIn(TABLE, state(true, [CARD]))).toBe(false);
    expect(surfaceShownIn(TABLE, state(true, [TABLE]))).toBe(true);
  });
});

// The release as shipped: the switch stays on, so the saved-input table and
// the mapping card show without a claim, flag OFF and ON. A gate check only;
// it checks no words.
describe("saved evaluation input preview scope as shipped (unreleased parts shown)", () => {
  it("shows the table and the mapping card without a claim, flag OFF and ON", () => {
    for (const basic of [false, true]) {
      const hidden = ["wiz:reads-table", "wiz:card-mapping-preview"].filter(
        (option) => !shown(option, state(basic)),
      );
      expect(hidden, `experimentsBasic=${basic}`).toEqual([]);
      expect(surfaceShownIn(TABLE, state(basic)), `basic=${basic}`).toBe(true);
    }
  });
});
