import { afterEach, describe, expect, it } from "vitest";
import { SURFACE_BY_ID } from "../launchSurfaces";
import { isSurfaceShown, setLaunchOverrideForTest } from "../launchSwitch";
import { MADE_REAL } from "../madeReal";
import { WIZARD_SETTING_SURFACE, wizardSettingShown } from "../wizardSettings";

// EXP-E2E-009: the server's V2 create saves a typed description and its V2
// update saves a changed name or description on their own. The XP claim, not
// the unreleased-parts switch, shows the wizard's description in a launch
// build; exp.rename still follows its parent, New experiment. The override
// turns the switch off, so the gates decide as they do with it off; no
// madeReal override, so the switch reads the committed claims.
const switchOff = (experimentsBasic = true) =>
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic,
    showUnreleased: false,
  });

afterEach(() => setLaunchOverrideForTest(null));

describe("exp.rename by its XP claim (EXP-E2E-009)", () => {
  it("is claimed in the XP slot", () => {
    expect(MADE_REAL.XP).toContain("exp.rename");
  });

  it("names its gate and owes none: the list's Edit saves on its own", () => {
    const { gate } = SURFACE_BY_ID["exp.rename"];
    expect(gate).toContain("wizardSettings.js description");
    expect(gate).toContain("RenameExperimentDialog.jsx");
    expect(gate).not.toContain("RENAME_NOT_AVAILABLE");
    expect(gate).not.toContain("before claiming");
  });

  it("shows in a launch build with unreleased parts hidden", () => {
    switchOff();
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.rename")).toBe(true);
  });

  it("shows the wizard's empty description setting by the same surface", () => {
    expect(WIZARD_SETTING_SURFACE.description).toBe("exp.rename");
    switchOff();
    expect(wizardSettingShown("description")).toBe(true);
  });

  it("stays dark with its parent while experimentsBasic is off", () => {
    switchOff(false);
    expect(isSurfaceShown("exp.new")).toBe(false);
    expect(isSurfaceShown("exp.rename")).toBe(false);
    expect(wizardSettingShown("description")).toBe(false);
  });
});
