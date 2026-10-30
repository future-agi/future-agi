import { describe, expect, it } from "vitest";
import {
  buildChangeCards,
  changeByCategory,
  formatPts,
} from "../insightsChanges";
import enrichedFixture from "./fixtures/run_sessions_failures_enriched.json";
import v2Fixture from "./fixtures/run_sessions_failures_v2.json";

const changes = enrichedFixture.result.result.changes;

describe("formatPts", () => {
  it("formats a share delta in signed percentage points", () => {
    expect(formatPts(0.12)).toBe("+12 pts");
    expect(formatPts(-0.031)).toBe("−3 pts");
    expect(formatPts(0.01)).toBe("+1 pt");
    expect(formatPts(0.004)).toBe("+<1 pt");
    expect(formatPts(0)).toBe("0 pts");
    expect(formatPts(NaN)).toBe("–");
  });
});

describe("buildChangeCards", () => {
  const cards = buildChangeCards(changes);

  it("puts new categories first, then significant moves by size, then the rest", () => {
    expect(cards.map((card) => card.key)).toEqual([
      "new-2d6dede91fc9",
      "metric-unresolved_rate",
      "cat-21d2b5dfc84e",
      "cat-3ffe0720fc9b",
      "cat-a55e9078defc",
      "metric-sentiment",
    ]);
  });

  it("describes a category move as from → to with its points", () => {
    expect(cards.find((c) => c.key === "cat-21d2b5dfc84e")).toMatchObject({
      kind: "category",
      direction: "up",
      from: "5%",
      to: "14%",
      deltaText: "+9 pts",
      significant: true,
      tone: "neutral",
    });
    expect(cards.find((c) => c.key === "cat-a55e9078defc")).toMatchObject({
      direction: "down",
      deltaText: "−3 pts",
      significant: false,
    });
  });

  it("gives metrics a good or bad direction", () => {
    expect(cards.find((c) => c.key === "metric-unresolved_rate")).toMatchObject(
      {
        from: "22%",
        to: "36%",
        deltaText: "+14 pts",
        tone: "worse",
      },
    );
    expect(cards.find((c) => c.key === "metric-sentiment")).toMatchObject({
      from: "2.9",
      to: "2.7",
      deltaText: "",
      tone: "worse",
      significant: false,
    });
  });

  it("gives a top and the sub that moved with it one card", () => {
    const move = { share: 0.3, previous_share: 0.1, delta: 0.2 };
    const changes = {
      categories: [
        {
          category_id: "t",
          name: "Refunds",
          level: 1,
          count: 9,
          previous_count: 3,
          ...move,
        },
        {
          category_id: "s",
          name: "Refund loops",
          level: 2,
          count: 9,
          previous_count: 3,
          ...move,
        },
        {
          category_id: "o",
          name: "Other sub",
          level: 2,
          count: 2,
          previous_count: 5,
          share: 0.05,
          previous_share: 0.1,
          delta: -0.05,
        },
        {
          category_id: "t2",
          name: "Billing",
          level: 1,
          count: 20,
          previous_count: 20,
          share: 0.4,
          previous_share: 0.4,
          delta: 0,
        },
        // Another top's sub that happens to move as "Refunds" did.
        {
          category_id: "s2",
          name: "Double charges",
          level: 2,
          count: 9,
          previous_count: 3,
          ...move,
        },
      ],
    };
    const parents = { s: "t", o: "t", s2: "t2" };
    const out = buildChangeCards(changes, (id) => parents[id]);
    expect(out.map((c) => c.categoryId).sort()).toEqual(["o", "s2", "t", "t2"]);
    // Without knowing a sub's top, no card is dropped.
    expect(buildChangeCards(changes)).toHaveLength(5);
  });

  it("gives a new top and its lone new sub one card", () => {
    // Leftovers first show up: "Other" and its one sub, both new.
    const fresh = { share: 0.2, previous_share: 0, delta: 0.2, is_new: true };
    const changes = {
      categories: [
        {
          category_id: "unmatched-top",
          name: "Other",
          level: 1,
          count: 12,
          previous_count: 0,
          ...fresh,
        },
        {
          category_id: "unmatched",
          name: "Didn't fit a category",
          level: 2,
          count: 12,
          previous_count: 0,
          ...fresh,
        },
      ],
      new_category_ids: ["unmatched-top", "unmatched"],
    };
    const out = buildChangeCards(changes, (id) =>
      id === "unmatched" ? "unmatched-top" : undefined,
    );
    expect(out.map((c) => [c.kind, c.categoryId, c.name])).toEqual([
      ["new", "unmatched-top", "Other"],
    ]);
  });

  it("is empty without changes and survives partial objects", () => {
    expect(buildChangeCards(undefined)).toEqual([]);
    expect(buildChangeCards({ new_category_ids: ["x"] })).toEqual([
      expect.objectContaining({ kind: "new", categoryId: "x" }),
    ]);
  });
});

describe("changeByCategory", () => {
  it("maps category ids to their delta for the table", () => {
    const map = changeByCategory(changes);
    expect(map.get("21d2b5dfc84e")).toMatchObject({
      delta: 0.089,
      significant: true,
      isNew: false,
    });
    expect(map.get("2d6dede91fc9").isNew).toBe(true);
    expect(changeByCategory(null).size).toBe(0);
  });
});

describe("a real run compared with the one before", () => {
  const real = v2Fixture.result.result.changes;

  it("keeps every move muted when none is significant, largest first", () => {
    const cards = buildChangeCards(real);
    expect(cards.some((card) => card.significant)).toBe(false);
    const points = cards
      .filter((card) => card.deltaText)
      .map((card) => Math.abs(card.delta));
    expect(points).toEqual([...points].sort((a, b) => b - a));
    expect(cards.find((c) => c.key === "metric-unresolved_rate")).toMatchObject(
      { from: "36%", to: "37%", deltaText: "+1 pt", tone: "worse" },
    );
  });

  it("treats a row flagged is_new as a new category", () => {
    const cards = buildChangeCards({
      categories: [
        {
          category_id: "n1",
          name: "Fresh",
          share: 0.1,
          previous_share: 0,
          delta: 0.1,
          count: 7,
          is_new: true,
          significant: false,
        },
      ],
    });
    expect(cards[0]).toMatchObject({ kind: "new", count: 7, to: "10%" });
    expect(
      changeByCategory({
        categories: [{ category_id: "n1", is_new: true }],
      }).get("n1").isNew,
    ).toBe(true);
  });
});
