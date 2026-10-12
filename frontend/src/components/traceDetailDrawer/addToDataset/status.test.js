import { describe, expect, it } from "vitest";
import { getAddToDatasetStatus } from "./status";

describe("getAddToDatasetStatus", () => {
  it("uses an in-progress message unless the API confirms completion", () => {
    expect(
      getAddToDatasetStatus(
        { status: "processing" },
        "Datapoints added to newly created dataset",
      ),
    ).toEqual({
      message:
        "Rows are still being added in the background. This can take a while for large selections.",
      variant: "info",
    });

    expect(
      getAddToDatasetStatus(
        undefined,
        "Datapoints added to newly created dataset",
      ),
    ).toEqual({
      message:
        "Rows are still being added in the background. This can take a while for large selections.",
      variant: "info",
    });
  });

  it("uses a success message only for a completed response", () => {
    expect(
      getAddToDatasetStatus({ status: "completed" }, "Data added successfully"),
    ).toEqual({
      message: "Data added successfully",
      variant: "success",
    });
  });
});
