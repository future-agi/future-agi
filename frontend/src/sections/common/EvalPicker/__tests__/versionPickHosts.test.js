// What the picker's version choice does on each host: the dataset,
// experiment and optimization hosts save a pick and run it; the task,
// simulation and workbench hosts run the default version, so the choice shows
// that version only. An experiment's new binding offers no choice while the
// new experiment pages are off.
import { afterEach, describe, expect, it } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  DEFAULT_VERSION_RUNS_LINE,
  VERSION_CHOICE_BY_SOURCE,
  versionChoiceFor,
} from "../versionPickHosts";

afterEach(() => setLaunchOverrideForTest(null));

describe("what the version choice does on each picker host", () => {
  it("maps each host to a pick or to the default version only", () => {
    expect(VERSION_CHOICE_BY_SOURCE).toEqual({
      dataset: "pick",
      experiment: "pick",
      "run-optimization": "pick",
      task: "default only",
      simulation: "default only",
      "create-simulate": "default only",
      workbench: "default only",
    });
  });

  it.each([
    ["dataset", "pick"],
    ["experiment", "pick"],
    ["task", "default only"],
    ["simulation", "default only"],
    ["create-simulate", "default only"],
    ["run-optimization", "pick"],
    ["workbench", "default only"],
    ["composite", null],
    ["custom", null],
    [undefined, null],
  ])("%s: %s", (source, choice) => {
    expect(versionChoiceFor(source)).toBe(choice);
    expect(versionChoiceFor(source, { editingBinding: true })).toBe(choice);
  });

  it("says why a version cannot be changed", () => {
    expect(DEFAULT_VERSION_RUNS_LINE).toBe(
      "A version can't be chosen here. This evaluation runs with its current settings.",
    );
  });

  it("offers an experiment's new binding no choice while the new experiment pages are off", () => {
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: false,
      madeReal: [],
    });
    expect(versionChoiceFor("experiment")).toBeNull();
    expect(versionChoiceFor("experiment", { editingBinding: true })).toBe(
      "pick",
    );
    expect(versionChoiceFor("dataset")).toBe("pick");
    expect(versionChoiceFor("run-optimization")).toBe("pick");
    expect(versionChoiceFor("task")).toBe("default only");
    expect(versionChoiceFor("workbench")).toBe("default only");

    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: true,
      madeReal: [],
    });
    expect(versionChoiceFor("experiment")).toBe("pick");
  });
});
