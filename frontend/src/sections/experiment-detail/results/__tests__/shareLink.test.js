import { describe, expect, it } from "vitest";
import { shareLinkOf, shareLinkSummary } from "../shareLink";

describe("shareLinkOf", () => {
  const href =
    "http://x.test/dashboard/develop/d/compare/data?runs=a,b&baseline=a&proto_stage=r4&from=%2Fback&show=regressed&row=r12&run=b&dtab=trajectory";
  it("drops from (the personal back-path), keeps proto_stage and the view", () => {
    const u = new URL(shareLinkOf(href));
    // IT-R1 G2 P6: the prototype stage is per tab; without it the link
    // opens on `fresh`, where these runs do not exist.
    expect(u.searchParams.get("proto_stage")).toBe("r4");
    expect(u.searchParams.get("from")).toBeNull();
    expect(u.searchParams.get("show")).toBe("regressed");
    expect(u.searchParams.get("row")).toBe("r12");
  });
  it("carries this tab's prototype stage when the URL has none", () => {
    const bare = "http://x.test/dashboard/develop/d/compare/data?runs=a,b";
    const u = new URL(shareLinkOf(bare, { stage: "r2-after-iterate" }));
    expect(u.searchParams.get("proto_stage")).toBe("r2-after-iterate");
    expect(new URL(shareLinkOf(bare, { stage: null })).search).toBe(
      "?runs=a%2Cb",
    );
  });
  it("drops the open row unless included", () => {
    const u = new URL(shareLinkOf(href, { includeRow: false }));
    ["row", "run", "dtab"].forEach((k) =>
      expect(u.searchParams.get(k)).toBeNull(),
    );
    expect(u.searchParams.get("runs")).toBe("a,b");
  });
});

describe("shareLinkSummary", () => {
  it("lists what the link holds", () => {
    expect(
      shareLinkSummary({
        runCount: 2,
        baselineLabel: "#2B",
        tab: "data",
        show: "regressed",
        rowLabel: "T12",
      }),
    ).toBe("2 runs · baseline #2B · Data · Worse filter · row T12 open");
    expect(shareLinkSummary({ runCount: 1, tab: "data", show: "all" })).toBe(
      "1 run · Data",
    );
    expect(
      shareLinkSummary({ runCount: 2, tab: "data", sort: "regressed" }),
    ).toBe("2 runs · Data · sorted by worse than baseline first");
  });
});
