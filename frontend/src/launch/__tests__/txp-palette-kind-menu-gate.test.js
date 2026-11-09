import { afterEach, describe, expect, it } from "vitest";
import {
  isSurfaceShown,
  isVerbShown,
  setLaunchOverrideForTest,
} from "../launchSwitch";
import { MADE_REAL } from "../madeReal";

// T-XP (release-lead 10-31 09:20): the dataset palette's New experiment entry
// and the New experiment kinds menu stay dark while experimentsBasic is OFF
// and show together when it is ON. The kinds menu is the 708 add.61a XP claim
// exp.new.kindMenu, Agent graph only: Already running stays unclaimed. No
// madeReal override here, so the switch reads the committed claims.
const PALETTE_ENTRY = "dataset.newExperiment";
const KIND_MENU = "exp.new.kindMenu";
const ALREADY_RUNNING = "exp.new.runningSources";

afterEach(() => setLaunchOverrideForTest(null));

describe("T-XP palette entry and kinds menu follow experimentsBasic", () => {
  it("OFF shows neither the palette entry nor the kinds menu", () => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    expect(isVerbShown(PALETTE_ENTRY)).toBe(false);
    expect(isSurfaceShown("exp.new")).toBe(false);
    expect(isSurfaceShown(KIND_MENU)).toBe(false);
    expect(isSurfaceShown(ALREADY_RUNNING)).toBe(false);
  });

  it("ON shows the palette entry and the kinds menu, Agent graph only", () => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
    expect(isVerbShown(PALETTE_ENTRY)).toBe(true);
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown(KIND_MENU)).toBe(true);
    expect(isSurfaceShown(ALREADY_RUNNING)).toBe(false);
  });

  it("claims the kinds menu in the XP slot and never Already running", () => {
    expect(MADE_REAL.XP).toContain(KIND_MENU);
    expect(Object.values(MADE_REAL).flat()).not.toContain(ALREADY_RUNNING);
  });
});
