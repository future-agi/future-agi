import { describe, expect, it } from "vitest";
import { zoomRange } from "../metricStripModel";

describe("zoomRange (EL#22)", () => {
  it("zooms a 53-91 series to 40-100", () => {
    expect(zoomRange([53.3, 65.6, 85.6, 91.0, 83.3])).toEqual({
      min: 40,
      max: 100,
      zoomed: true,
    });
  });
  it("keeps 0-100 when the data spans it or is empty", () => {
    expect(zoomRange([2, 98])).toEqual({ min: 0, max: 100, zoomed: false });
    expect(zoomRange([null, undefined])).toEqual({
      min: 0,
      max: 100,
      zoomed: false,
    });
  });
  it("never collapses a flat series to a zero-height axis", () => {
    const r = zoomRange([70, 70]);
    expect(r.max - r.min).toBeGreaterThanOrEqual(10);
  });
});
