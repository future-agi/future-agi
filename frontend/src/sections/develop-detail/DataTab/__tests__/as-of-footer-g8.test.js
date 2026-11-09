// v2.1 g8 (versions:as-of, proof walk #6): the footer of `?version=N` counts
// that version's rows, not latest's.
import { describe, expect, it } from "vitest";
import { asOfFooterCounts } from "../asOfFooterCounts";
import { dataFooterText } from "../DataGrid/dataHelp";

describe("as-of footer counts", () => {
  it("as of v3 the total is v3's row_count, so an unfiltered grid is not 'filtered'", () => {
    const c = asOfFooterCounts({
      asOfVersion: 3,
      versionRowCount: 240,
      latestTotal: 248,
      shown: 240,
      totalRows: 240,
    });
    expect(c.total).toBe(240);
    const text = dataFooterText({ total: c.total, shown: 240, version: 3 });
    expect(text).not.toMatch(/248|filtered/);
    expect(text).toMatch(/v3/);
  });

  it("as of v3 with a filter compares with v3's size", () => {
    const c = asOfFooterCounts({
      asOfVersion: 3,
      versionRowCount: 240,
      latestTotal: 248,
      shown: 12,
    });
    expect(dataFooterText({ total: c.total, shown: 12, version: 3 })).toMatch(
      /^12 of 240 .*\(filtered\)/,
    );
  });

  it("falls back to the version's own as-of read, then to the grid", () => {
    expect(
      asOfFooterCounts({
        asOfVersion: 2,
        asOfTotal: 230,
        latestTotal: 248,
        shown: 230,
      }).total,
    ).toBe(230);
    expect(
      asOfFooterCounts({ asOfVersion: 2, latestTotal: 248, shown: 230 }).total,
    ).toBe(230);
  });

  it("latest keeps the larger of the page-0 total and the grid", () => {
    expect(asOfFooterCounts({ latestTotal: 248, shown: 254 }).total).toBe(254);
    expect(asOfFooterCounts({ latestTotal: 248, shown: 12 }).total).toBe(248);
  });
});
