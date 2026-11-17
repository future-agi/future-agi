// v2.1 g8 (entry:run-on-rows:training-split, proof walk #3): Training set ›
// New experiment opens on the export's own version and its test split.
import { describe, expect, it } from "vitest";
import { parseRunOnRows } from "src/sections/common/RunSources/runOnRows";
import { trainingSetDoor } from "../trainingDoor";

describe("training-set door", () => {
  it("prefills the export's version and the test split", () => {
    const door = trainingSetDoor({
      datasetId: "ds-1",
      testName: "test",
      version: { id: "ver-3", number: 3 },
    });
    expect(door.prefill).toEqual({ version_id: "ver-3", split_name: "test" });
    const deep = parseRunOnRows(new URL(door.href, "http://x").search);
    expect(deep.open).toBe(true);
    expect(deep.split).toBe("test");
    // With the owed g2 runOnRows patch the link names the version too.
    if ("datasetVersionId" in deep) expect(deep.datasetVersionId).toBe("ver-3");
  });

  it("with no saved version the door says only the split", () => {
    const door = trainingSetDoor({ datasetId: "ds-1", testName: "holdout" });
    expect(door.prefill).toEqual({ split_name: "holdout" });
    expect(
      new URL(door.href, "http://x").searchParams.get("version_id"),
    ).toBeNull();
  });
});
