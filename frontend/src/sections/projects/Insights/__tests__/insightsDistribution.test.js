import { describe, expect, it } from "vitest";
import {
  CATEGORY_PALETTE,
  OTHER_COLOR,
  OTHER_ID,
  buildCategoryTree,
  buildColorOrder,
  buildSegments,
  buildTrend,
  colorFor,
  isMutedCategory,
  resultTops,
  sortCategoryTree,
} from "../insightsDistribution";
import runFixture from "./fixtures/run_sessions_failures.json";

const categories = runFixture.result.result.categories;

const run = (id, createdAt, distribution, extra = {}) => ({
  id,
  created_at: createdAt,
  status: "completed",
  distribution: distribution.map(([category_id, name, count]) => ({
    category_id,
    name,
    count,
    share: 0,
    status: name === "Other" ? "unmatched" : "active",
  })),
  ...extra,
});

describe("buildColorOrder", () => {
  it("puts the saved categories first and skips muted ones", () => {
    const order = buildColorOrder({
      taxonomy: {
        tops: [
          { id: "b", name: "Beta" },
          { id: "o", name: "Other" },
        ],
      },
      runs: [run("r1", "2026-10-01", [["a", "Alpha", 10]])],
    });
    expect(order).toEqual(["b", "a"]);
  });

  it("keeps a category's colour across runs whatever its rank", () => {
    const runs = [
      run("r2", "2026-10-02", [
        ["y", "Y", 50],
        ["x", "X", 5],
      ]),
      run("r1", "2026-10-01", [
        ["x", "X", 30],
        ["y", "Y", 10],
      ]),
    ];
    const order = buildColorOrder({ runs });
    expect(order).toEqual(["x", "y"]);
    expect(colorFor("x", order, "light")).toBe(CATEGORY_PALETTE.light[0]);
    expect(colorFor("x", order, "dark")).toBe(CATEGORY_PALETTE.dark[0]);
  });

  it("gives no hue past the eighth slot", () => {
    const order = Array.from({ length: 9 }, (_, i) => `c${i}`);
    expect(colorFor("c8", order)).toBeNull();
  });
});

describe("buildSegments", () => {
  const items = [
    { id: "a", name: "A", count: 40 },
    { id: "b", name: "B", count: 30 },
    { id: "c", name: "C", count: 20 },
    { id: "d", name: "D", count: 5 },
    { id: "u", name: "Other", count: 5, status: "unmatched" },
  ];
  const order = ["a", "b", "c", "d"];

  it("orders by size, greys the unmatched bucket and sums to one", () => {
    const segments = buildSegments(items, { order });
    expect(segments.map((s) => s.id)).toEqual(["a", "b", "c", "d", "u"]);
    expect(segments.at(-1)).toMatchObject({
      name: "Other",
      color: OTHER_COLOR.light,
      muted: true,
    });
    expect(segments.reduce((sum, s) => sum + s.share, 0)).toBeCloseTo(1);
  });

  it("folds the tail into Other when capped for a card legend", () => {
    const segments = buildSegments(items, { order, max: 3 });
    expect(segments.map((s) => s.name)).toEqual(["A", "B", "Other"]);
    expect(segments[2]).toMatchObject({ id: OTHER_ID, count: 30 });
  });
});

describe("buildTrend", () => {
  it("lists completed runs oldest first", () => {
    const rows = buildTrend(
      [
        run("new", "2026-10-03", [["a", "A", 6]]),
        run("failed", "2026-10-02", [["a", "A", 6]], { status: "failed" }),
        run("old", "2026-10-01", [["a", "A", 3]], { analysed_count: 3 }),
      ],
      { order: ["a"] },
    );
    expect(rows.map((r) => r.runId)).toEqual(["old", "new"]);
    expect(rows[0].count).toBe(3);
    expect(rows[1].segments[0].color).toBe(CATEGORY_PALETTE.light[0]);
  });
});

describe("category tree", () => {
  it("nests subcategories under their top", () => {
    const tree = buildCategoryTree(categories);
    expect(tree).toHaveLength(3);
    const unresponsive = tree.find((row) => row.id === "3ffe0720fc9b");
    expect(unresponsive.expandable).toBe(true);
    expect(unresponsive.childRows.map((c) => c.id)).toEqual([
      "21d2b5dfc84e",
      "b84f080935f0",
      "dbabb1b07db7",
      "2d6dede91fc9",
    ]);
  });

  it("reads a top with one identical child as a single level", () => {
    const tree = buildCategoryTree([
      {
        id: "t",
        level: 1,
        has_children: true,
        children: ["s"],
        stats: { count: 4 },
      },
      { id: "s", level: 2, parent_id: "t", stats: { count: 4 } },
    ]);
    expect(tree[0].expandable).toBe(false);
  });

  it("sorts by any column and keeps the unmatched bucket last", () => {
    const tree = buildCategoryTree([
      ...categories,
      {
        id: "unmatched-top",
        name: "Other",
        level: 1,
        status: "unmatched",
        stats: { count: 99, users: 99 },
      },
    ]);
    const byCount = sortCategoryTree(tree, "count", "desc");
    expect(byCount.at(-1).id).toBe("unmatched-top");
    expect(byCount[0].id).toBe("a55e9078defc");
    const byUsers = sortCategoryTree(tree, "users", "asc");
    expect(byUsers[0].stats.users).toBeLessThanOrEqual(byUsers[1].stats.users);
    expect(isMutedCategory(byUsers.at(-1))).toBe(true);
  });

  it("reads top-level items off a result", () => {
    expect(resultTops(categories).map((t) => t.count)).toEqual([28, 18, 26]);
  });
});
