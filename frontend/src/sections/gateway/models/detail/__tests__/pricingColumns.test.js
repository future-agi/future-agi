import { describe, expect, it } from "vitest";

import { pricingColumns } from "../pricingColumns";

// At 390 px the Pricing table scrolled sideways inside its
// card, so "Managed price", the price the user pays, started off screen and
// the header read "Provider …". The table now has one price column (what the
// organization pays), so no provider price or margin sits beside it. The
// card's content box at 390 px is 322 px
// (390 - the frame's 2 x 16 px - the section's 2 x 16 px padding and its
// border, and the grid's border).
const CONTENT_AT_390 = 322;

describe("model page Pricing columns", () => {
  it("fit a 390 px screen with one price: the credits price when sold, else the provider's", () => {
    const cols = pricingColumns(true);
    expect(cols.map((c) => c.headerName)).toEqual(["Per 1M tokens", "Price"]);
    expect(cols.map((c) => c.field)).toEqual(["label", "managed"]);
    expect(pricingColumns(false).map((c) => c.field)).toEqual([
      "label",
      "provider",
    ]);
    for (const col of [...cols, ...pricingColumns(true, { inclusive: true })]) {
      expect(col.headerName).not.toMatch(/markup|provider|future agi price|%/i);
    }
    const min = cols.reduce((sum, c) => sum + (c.minWidth || 0), 0);
    expect(min).toBeLessThanOrEqual(CONTENT_AT_390);
  });

  it("wrap a long header or label instead of cutting it", () => {
    for (const col of pricingColumns(true)) {
      expect(col.wrapHeaderText, col.headerName).toBe(true);
      expect(col.autoHeaderHeight, col.headerName).toBe(true);
    }
    const label = pricingColumns(true).find((c) => c.field === "label");
    expect(label.wrapText).toBe(true);
    expect(label.autoHeight).toBe(true);
  });
});
