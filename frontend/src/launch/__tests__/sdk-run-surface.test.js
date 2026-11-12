import { describe, expect, it } from "vitest";
import { surfaceShownIn } from "../launchSwitch";
import { SURFACE_BY_ID } from "../launchSurfaces";

const state = (experimentsBasic, madeReal = []) => ({
  launch: true, experimentsBasic, madeReal: new Set(madeReal),
});

describe("SDK run traces launch surface", () => {
  it("stays off independently of the experiments flag", () => {
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
