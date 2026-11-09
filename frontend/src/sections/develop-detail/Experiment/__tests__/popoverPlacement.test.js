import { describe, expect, it } from "vitest";
import {
  POPOVER_MAX,
  POPOVER_MIN,
  popoverPlacement,
} from "../popoverPlacement";

// 1280x800 New experiment drawer: the scroll body runs 130 → 733 (footer).
const BODY = { top: 130, bottom: 733 };

describe("popoverPlacement", () => {
  it("opens below when the content fits there", () => {
    expect(popoverPlacement({ top: 300, bottom: 340 }, BODY, 324)).toEqual({
      place: "below",
      maxHeight: 324,
    });
  });

  it("flips above when only the space above fits", () => {
    const r = popoverPlacement({ top: 560, bottom: 640 }, BODY, 324);
    expect(r).toEqual({ place: "above", maxHeight: 324 });
  });

  it("takes the roomier side and caps the height to it (never over the footer)", () => {
    // The Prompt card at 1280x800: 283 px below, 297 px above, 324 wanted.
    const r = popoverPlacement({ top: 435, bottom: 442 }, BODY, 324);
    expect(r.place).toBe("above");
    expect(r.maxHeight).toBe(297);
    const below = popoverPlacement({ top: 150, bottom: 442 }, BODY, 400);
    expect(below.place).toBe("below");
    expect(below.maxHeight).toBe(283);
    expect(442 + 8 + below.maxHeight).toBeLessThanOrEqual(BODY.bottom);
  });

  it("caps at the max and never shrinks below the min", () => {
    expect(
      popoverPlacement({ top: 140, bottom: 160 }, BODY, 2000).maxHeight,
    ).toBe(POPOVER_MAX);
    expect(
      popoverPlacement({ top: 140, bottom: 160 }, BODY, 20).maxHeight,
    ).toBe(POPOVER_MIN);
    expect(
      popoverPlacement({ top: 150, bottom: 700 }, { top: 140, bottom: 720 })
        .maxHeight,
    ).toBe(POPOVER_MIN);
  });

  it("defaults the wanted height to the max", () => {
    expect(popoverPlacement({ top: 100, bottom: 120 }, BODY)).toEqual({
      place: "below",
      maxHeight: POPOVER_MAX,
    });
  });
});
