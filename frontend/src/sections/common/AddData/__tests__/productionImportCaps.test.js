import { afterEach, expect, it } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { datasetCaps } from "src/sections/develop/realBackendCaps";

afterEach(() => setLaunchOverrideForTest(null));
it("enables the production palette command only with its own capability", () => {
  for (const [madeReal, expected] of [
    [[], false],
    [["dataset.addRows"], false],
    [["dataset.fromProduction"], true],
  ]) {
    setLaunchOverrideForTest({
      launch: true,
      // The released set only: a launch build otherwise shows every unreleased part
      // (launchSwitch SHOW_UNRELEASED_PARTS), which would offer the command without its capability.
      showUnreleased: false,
      experimentsBasic: false,
      madeReal,
    });
    expect(datasetCaps(true).listFromTracesCommand).toBe(expected);
  }
});
