import { afterEach, describe, expect, it } from "vitest";
import {
  isSurfaceShown,
  isVerbShown,
  setLaunchOverrideForTest,
} from "../launchSwitch";
import { MADE_REAL } from "../madeReal";

// The dataset palette's New experiment entry and the New experiment kinds
// menu stay dark while experimentsBasic is OFF and show together when it
// is ON. XP claims the kinds menu; SRC claims Already running. No madeReal
// override here, so the switch reads the committed claims.
const PALETTE_ENTRY = "dataset.newExperiment";
const KIND_MENU = "exp.new.kindMenu";
const ALREADY_RUNNING = "exp.new.runningSources";

afterEach(() => setLaunchOverrideForTest(null));
// The switch ships on, so every surface shows (the last block below). The
// OFF and ON cases turn it off in their overrides, so the gates decide as
// they do with the switch off.

describe("palette entry and kinds menu follow experimentsBasic", () => {
  it("OFF shows neither the palette entry nor the kinds menu", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      showUnreleased: false,
    });
    expect(isVerbShown(PALETTE_ENTRY)).toBe(false);
    expect(isSurfaceShown("exp.new")).toBe(false);
    expect(isSurfaceShown(KIND_MENU)).toBe(false);
    expect(isSurfaceShown(ALREADY_RUNNING)).toBe(false);
  });

  it("ON shows the palette entry and the kinds menu", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      showUnreleased: false,
    });
    expect(isVerbShown(PALETTE_ENTRY)).toBe(true);
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown(KIND_MENU)).toBe(true);
    expect(isSurfaceShown(ALREADY_RUNNING)).toBe(true);
  });

  it("claims the kinds menu and Already running under separate owners", () => {
    expect(MADE_REAL.XP).toContain(KIND_MENU);
    expect(MADE_REAL.XP).not.toContain(ALREADY_RUNNING);
    expect(MADE_REAL.SRC).toContain(ALREADY_RUNNING);
  });
});

// The release as shipped: the switch stays on, so the palette entry, the New
// experiment wizard and its kinds menu show with experimentsBasic OFF or ON.
// A gate check only; it checks no words.
describe("palette entry and kinds menu follow experimentsBasic as shipped (unreleased parts shown)", () => {
  it("shows the palette entry, the wizard and the kinds menu, OFF or ON", () => {
    for (const experimentsBasic of [false, true]) {
      setLaunchOverrideForTest({ launch: true, experimentsBasic });
      const at = `experimentsBasic=${experimentsBasic}`;
      expect(isVerbShown(PALETTE_ENTRY), at).toBe(true);
      expect(isSurfaceShown("exp.new"), at).toBe(true);
      expect(isSurfaceShown(KIND_MENU), at).toBe(true);
    }
  });
});
