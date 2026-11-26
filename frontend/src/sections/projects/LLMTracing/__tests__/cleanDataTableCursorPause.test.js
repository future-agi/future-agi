import { describe, expect, it } from "vitest";

import cleanDataTableCss from "src/styles/clean-data-table.css?raw";

describe("paused server-side grid styling", () => {
  it("hides AG Grid's literal ERR skeleton while exact continuation is paused", () => {
    expect(cleanDataTableCss).toMatch(
      /\.clean-data-table\.ag-grid-cursor-paused\s+\.ag-skeleton-container\s*\{[\s\S]*?visibility:\s*hidden;/,
    );
  });
});
