import { afterEach, describe, expect, it } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { wizardSettingShown } from "src/launch/wizardSettings";
import { splitScopeShown } from "../splitScopeVisibility";
import { surfacesOfOption } from "src/launch/launchSurfaces";
import { valuesFromExperiment } from "../experimentFormValues";
import { runScopeBody } from "../runScopeBody";
import { wireExperimentBody } from "../serverBody";

const id = "11111111-1111-4111-8111-111111111111";
afterEach(() => setLaunchOverrideForTest(null));

describe("experiment split scope", () => {
  it("omits an empty wizard selection rather than submitting an empty scope", () => {
    const input = { split_ids: [], prompt_config: [] };
    expect(runScopeBody(input)).not.toHaveProperty("split_ids");
    expect(input.split_ids).toEqual([]);
  });

  it("sends a selected split without dropping or blocking it", () => {
    const wire = wireExperimentBody({ split_ids: [id] }, {}, false);
    expect(wire.blocks).toEqual([]);
    expect(wire.body.split_ids).toEqual([id]);
  });

  it("restores the persisted split scope for a copied experiment", () => {
    expect(
      valuesFromExperiment(
        { run_scope: { split_ids: [id] } },
        { mode: "duplicate" },
      ).splitIds,
    ).toEqual([id]);
  });

  it("does not expose wizard splits just because dataset splits are claimed", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: ["dataset.splits"],
    });
    expect(wizardSettingShown("splits")).toBe(false);
    expect(splitScopeShown()).toBe(false);
    for (const option of [
      "data:rows:split",
      "estimate:smoke-split-suggestion",
      "entry:run-on-rows:training-split",
    ])
      expect(
        surfacesOfOption(option)
          .map((surface) => surface.id)
          .filter((id) => id !== "exp.new"),
      ).toEqual(["exp.new.splitScope"]);
  });

  it("keeps the independent setting inside the separately gated wizard", () => {
    // All-on F50: out of the 749 hold, the run-scope claim alone shows the
    // setting; the wizard that hosts it keeps its own gate.
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: ["exp.new.splitScope"],
    });
    expect(wizardSettingShown("splits")).toBe(true);
    expect(splitScopeShown()).toBe(true);
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: ["exp.new.splitScope"],
    });
    expect(wizardSettingShown("splits")).toBe(true);
    expect(splitScopeShown()).toBe(true);
    expect(isSurfaceShown("exp.new")).toBe(false);
  });
});
