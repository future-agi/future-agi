import { describe, expect, it } from "vitest";
import { MADE_REAL, madeRealSurfaceIds } from "src/launch/madeReal";
import { surfaceShownIn } from "src/launch/launchSwitch";
import { sanitizeLaunchSearch } from "src/launch/launchSearch";

const state = (claimed) => ({
  launch: true,
  experimentsBasic: false,
  madeReal: new Set(claimed ? ["dataset.filtersSystem.pills"] : []),
});

describe("failed-filter capability isolation", () => {
  it("registers the proven pill in the grid slot", () => {
    expect(MADE_REAL.GRID).toContain("dataset.filtersSystem.pills");
    expect(
      surfaceShownIn("dataset.filtersSystem.pills", {
        ...state(false),
        madeReal: madeRealSurfaceIds(),
      }),
    ).toBe(true);
  });

  it("does not expose expressions, versions, splits or saved views", () => {
    expect(surfaceShownIn("dataset.filtersSystem.pills", state(false))).toBe(
      false,
    );
    expect(surfaceShownIn("dataset.filtersSystem.pills", state(true))).toBe(
      true,
    );
    for (const id of [
      "dataset.filtersSystem.expressions",
      "dataset.versions",
      "dataset.splits",
      "dataset.savedViews",
    ]) {
      expect(surfaceShownIn(id, state(true)), id).toBe(false);
    }
  });

  it("retains failed-filter links only with the claim, without retaining deferred parameters", () => {
    const query =
      "?failed=1&fevals=quality&expr=value&splits=training&version=1";
    const path = "/dashboard/develop/filter-dataset";
    expect(sanitizeLaunchSearch(query, path, state(true)).search).toBe(
      "?failed=1&fevals=quality",
    );
    expect(sanitizeLaunchSearch(query, path, state(false)).search).toBe("");
  });
});
