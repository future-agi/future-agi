// The real backend and launch switch agree. NOHIDE restores legacy sheet
// doors (broken paths visibly disabled); NEW paste retains its own gate.
// The palette's Add row and Import rows keep their existing claim gates.
import { afterEach, describe, expect, it } from "vitest";
import {
  isRealBackend,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";
import {
  addRowsTilesIn,
  setLaunchOverrideForTest,
  surfaceShownIn,
  verbShownIn,
} from "src/launch/launchSwitch";
import { MADE_REAL, madeRealSurfaceIds } from "src/launch/madeReal";
import { ADD_ROWS_OPTIONS } from "src/sections/develop/importOptions";

afterEach(() => {
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
});

const LAUNCH = {
  launch: true,
  experimentsBasic: false,
  madeReal: madeRealSurfaceIds(),
};

describe("L1 the real set is L6's launch build", () => {
  it("follows isLaunchBuild unless an L1 test forces it", () => {
    setLaunchOverrideForTest({ launch: true });
    expect(isRealBackend()).toBe(true);
    setLaunchOverrideForTest({ launch: false });
    expect(isRealBackend()).toBe(false);
    setRealBackendForTests(true);
    expect(isRealBackend()).toBe(true);
  });

  it("claims the list and the classic grid writes in L1's slot", () => {
    expect(MADE_REAL.L1).toEqual(["list.real", "dataset.gridWrites"]);
    expect(surfaceShownIn("list.real", LAUNCH)).toBe(true);
    expect(surfaceShownIn("dataset.gridWrites", LAUNCH)).toBe(true);
  });

  it("shows legacy Add rows doors without exposing NEW paste", () => {
    const unclaimed = {
      ...LAUNCH,
      madeReal: new Set(["list.real", "dataset.gridWrites"]),
    };
    expect(surfaceShownIn("dataset.addRows", unclaimed)).toBe(true);
    expect(surfaceShownIn("dataset.addBlankRow", unclaimed)).toBe(false);
    expect(verbShownIn("grid.addRow", unclaimed)).toBe(false);
    expect(verbShownIn("grid.importRows", unclaimed)).toBe(false);
    // Census G2: retain every legacy door; visibility is not enablement.
    // Rendered availability and click guards are pinned in nohide-import.
    expect(
      addRowsTilesIn(ADD_ROWS_OPTIONS, unclaimed).map(({ id }) => id),
    ).toEqual([
      "uploadFile",
      "addFromExistingDataset",
      "importFromHuggingface",
      "empty-row",
    ]);

    const claimed = {
      ...unclaimed,
      madeReal: new Set([
        ...unclaimed.madeReal,
        "dataset.addRows",
        "dataset.addBlankRow",
      ]),
    };
    expect(surfaceShownIn("dataset.addRows", claimed)).toBe(true);
    expect(surfaceShownIn("dataset.addBlankRow", claimed)).toBe(true);
    expect(verbShownIn("grid.addRow", claimed)).toBe(true);
    expect(verbShownIn("grid.importRows", claimed)).toBe(false);
    expect(
      addRowsTilesIn(ADD_ROWS_OPTIONS, claimed).map(({ id }) => id),
    ).toEqual([
      "uploadFile",
      "addFromExistingDataset",
      "importFromHuggingface",
      "empty-row",
    ]);
    const pasteClaimed = {
      ...claimed,
      madeReal: new Set([...claimed.madeReal, "dataset.imports.pasteRows"]),
    };
    expect(
      addRowsTilesIn(ADD_ROWS_OPTIONS, pasteClaimed).map(({ id }) => id),
    ).toEqual([
      "uploadFile",
      "paste",
      "addFromExistingDataset",
      "importFromHuggingface",
      "empty-row",
    ]);
  });

  it("keeps the list's organise surface hidden (no claim on it)", () => {
    expect(surfaceShownIn("list.organize.folders", LAUNCH)).toBe(false);
  });
});
