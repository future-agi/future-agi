import { afterEach, describe, expect, it } from "vitest";
import { setLaunchOverrideForTest, surfaceShownIn } from "../launchSwitch";
import { SURFACE_BY_ID } from "../launchSurfaces";

const state = (experimentsBasic, madeReal = []) => ({
  launch: true, experimentsBasic, madeReal: new Set(madeReal),
});

afterEach(() => setLaunchOverrideForTest(null));
// The switch ships on, so every surface shows (the last block below). The
// cases that pin what the launch gates hide turn it off in the override
// first, so the gates decide as they do with the switch off.
const switchOff = () => setLaunchOverrideForTest({ showUnreleased: false });

describe("SDK run traces launch surface", () => {
  it("stays off independently of the experiments flag", () => {
    switchOff();
    expect(surfaceShownIn("dataset.sdkRunTraces", state(false))).toBe(false);
    expect(surfaceShownIn("dataset.sdkRunTraces", state(true))).toBe(false);
    expect(SURFACE_BY_ID["dataset.sdkRunTraces"].ships).toBe("hidden");
  });
  it("can be qualified independently without enabling experiments", () => {
    expect(surfaceShownIn("dataset.sdkRunTraces", state(false, ["dataset.sdkRunTraces"]))).toBe(true);
    expect(SURFACE_BY_ID["dataset.sdkRunTraces"].parent).toBeUndefined();
    expect(SURFACE_BY_ID["dataset.sdkRunTraces"].experiments).toBeUndefined();
  });
  it("pins the run deep link to dataset pages only", () => {
    const [entry] = SURFACE_BY_ID["dataset.sdkRunTraces"].entries.search;
    expect(entry.key).toBe("run");
    expect(entry.on.test("/dashboard/develop/dataset-1")).toBe(true);
    expect(entry.on.test("/dashboard/develop/experiment/exp-1")).toBe(false);
    expect(entry.on.test("/dashboard/gateway/runs")).toBe(false);
  });
});

// The release as shipped: the switch stays on, so SDK run traces show with
// the experiments flag off or on and no claim. A gate check only; it checks
// no words.
describe("SDK run traces launch surface as shipped (unreleased parts shown)", () => {
  it("shows with the experiments flag off or on, without a claim", () => {
    for (const basic of [false, true])
      expect(
        surfaceShownIn("dataset.sdkRunTraces", state(basic)),
        `basic=${basic}`,
      ).toBe(true);
  });
});
