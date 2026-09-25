import { describe, expect, it } from "vitest";
import { getLineSeriesName } from "../common";

describe("getLineSeriesName", () => {
  it("names latency as the median it is, never an average", () => {
    expect(getLineSeriesName("latency")).toMatch(/\(Median Latency\)$/);
    expect(getLineSeriesName("latency")).not.toMatch(/Avg/);
  });

  it("keeps cost as an average", () => {
    expect(getLineSeriesName("cost")).toMatch(/\(Avg\. Cost\)$/);
  });
});
