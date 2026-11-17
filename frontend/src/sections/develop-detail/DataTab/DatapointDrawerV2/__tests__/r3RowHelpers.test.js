import { describe, expect, it, vi } from "vitest";

vi.mock("src/sections/common/Metrics/metricFormat", () => ({
  formatValue: (_d, v) => String(v),
}));

import {
  expectedSourceLabel,
  isEmptyValue,
  versionPillTooltip,
} from "../rowModel";
import { scrollPlacement } from "../useRowNavigation";
import {
  defaultSideBySideRuns,
  filterRunsByVerdict,
  orderForSideBySide,
  sideBySideBaseline,
  verdictSegments,
} from "../runsModel";

describe("isEmptyValue (RV#27)", () => {
  it("treats blank strings, empty JSON and null as empty", () => {
    ["", "  ", "[]", "{}", "null", null, undefined, [], {}].forEach((v) =>
      expect(isEmptyValue(v)).toBe(true),
    );
  });
  it("keeps real values", () => {
    ["a", "[1]", 0, false, [1], { a: 1 }].forEach((v) =>
      expect(isEmptyValue(v)).toBe(false),
    );
  });
});

describe("versionPillTooltip (RV#17)", () => {
  const versions = [
    {
      number: 1,
      created_by: { name: "Asha Rao" },
      created_at: "2026-09-26T04:42:00Z",
    },
    {
      number: 4,
      created_by: { name: "Meera Iyer" },
      created_at: "2026-10-02T04:42:00Z",
    },
  ];
  it("names who added and last edited the row", () => {
    const t = versionPillTooltip(
      { added_in_version: 1, last_changed_in_version: 4 },
      versions,
    );
    expect(t).toMatch(/^Added in v1 by Asha Rao, /);
    expect(t).toMatch(/Last edited in v4 \(Meera Iyer, /);
    expect(t).toMatch(/Click for the version history\.$/);
  });
  it("skips the edit sentence when it was never edited", () => {
    expect(versionPillTooltip({ added_in_version: 4 }, [])).toBe(
      "Added in v4. Click for the version history.",
    );
  });
});

describe("expectedSourceLabel (R2D-22)", () => {
  it("labels known and unknown sources", () => {
    expect(expectedSourceLabel({ kind: "replay" })).toBe(
      "Expected from a replay",
    );
    expect(expectedSourceLabel("production_output")).toBe(
      "Expected = production output",
    );
    expect(expectedSourceLabel({ kind: "sdk" })).toBe("Expected from sdk");
  });
});

describe("scrollPlacement (RV#9)", () => {
  const api = { getVerticalPixelRange: () => ({ top: 0, bottom: 360 }) };
  const node = (rowTop) => ({ rowTop, rowHeight: 36 });
  it("centres a row near the bottom or top edge", () => {
    expect(scrollPlacement(api, node(300))).toBe("middle");
    expect(scrollPlacement(api, node(36))).toBe("middle");
    expect(scrollPlacement(api, node(900))).toBe("middle");
  });
  it("leaves a row in the middle band alone", () => {
    expect(scrollPlacement(api, node(144))).toBeNull();
  });
  it("is null without a grid", () => {
    expect(scrollPlacement(null, node(0))).toBeNull();
  });
});

describe("side-by-side door (MUST 1, RV#21)", () => {
  const metric = { key: "c", output_type: "pass_fail", higher_is_better: true };
  const run = (id, v, extra = {}) => ({
    run_id: id,
    label: `#${id}`,
    evals: v == null ? {} : { c: { value: v } },
    ...extra,
  });
  const runs = [
    run("1A", 1, { is_dataset_baseline: true }),
    run("2A", 1),
    run("3A", 0),
    run("4A", null),
    run("5A", 0),
    run("6A", 1),
  ];
  it("segments only scored runs, in order", () => {
    expect(verdictSegments(runs, metric).map((s) => s.status)).toEqual([
      "pass",
      "pass",
      "fail",
      "fail",
      "pass",
    ]);
  });
  it("filters by verdict", () => {
    expect(
      filterRunsByVerdict(runs, metric, "fail").map((r) => r.run_id),
    ).toEqual(["3A", "5A"]);
    expect(filterRunsByVerdict(runs, metric, "all")).toHaveLength(6);
  });
  it("defaults to the baseline + runs that disagree, max 4", () => {
    expect(defaultSideBySideRuns(runs, metric)).toEqual([
      "1A",
      "3A",
      "5A",
      "2A",
    ]);
    expect(defaultSideBySideRuns([], metric)).toEqual([]);
  });
  it("puts the dataset baseline first when it is picked", () => {
    expect(sideBySideBaseline(["3A", "1A"], runs)).toBe("1A");
    expect(sideBySideBaseline(["5A", "3A"], runs)).toBe("5A");
    expect(orderForSideBySide(["5A", "1A", "3A"], runs)).toEqual([
      "1A",
      "3A",
      "5A",
    ]);
  });
});
