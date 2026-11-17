// W5 B4: the chart board's URL round trip, limits and card actions.
import { describe, expect, it } from "vitest";
import {
  ALL_METRICS,
  BOARD_CHARTS,
  BOARD_MAX,
  addCard,
  cardTitle,
  duplicateCard,
  moveCard,
  parseBoard,
  removeCard,
  serializeBoard,
  toggleFull,
} from "../boardModel";

const A = { measure: "eval:tone", chart: "bars", rows: "meta:difficulty" };
const B = { measure: "cost", chart: "bars", rows: "none", full: true };

describe("board URL state", () => {
  it("round-trips cards in order", () => {
    const s = serializeBoard([A, B]);
    expect(s).toBe("eval:tone~bars~meta:difficulty~1|cost~bars~none~2");
    const cards = parseBoard(s);
    expect(
      cards.map(({ measure, chart, rows, full }) => ({
        measure,
        chart,
        rows,
        full,
      })),
    ).toEqual([{ ...A, full: false }, B]);
    expect(serializeBoard(cards)).toBe(s);
  });
  it("drops unknown charts and empty input; caps at BOARD_MAX", () => {
    expect(parseBoard("")).toEqual([]);
    expect(parseBoard("x~pie~none~1|cost~bars~none~1")).toHaveLength(1);
    const many = Array.from(
      { length: BOARD_MAX + 3 },
      () => "cost~bars~none~1",
    ).join("|");
    expect(parseBoard(many)).toHaveLength(BOARD_MAX);
    expect(serializeBoard([])).toBe("");
  });
  it("separators in values cannot break the encoding", () => {
    const s = serializeBoard([
      { measure: "a|b~c", chart: "bars", rows: "none" },
    ]);
    expect(parseBoard(s)[0].measure).toBe("abc");
  });
});

describe("card actions", () => {
  const cards = parseBoard(serializeBoard([A, B]));
  it("add, duplicate, full width, remove, move", () => {
    expect(addCard(cards, { measure: "steps", chart: "heatmap" })).toHaveLength(
      3,
    );
    expect(addCard(cards, { measure: "steps", chart: "pie" })).toBe(cards);
    expect(duplicateCard(cards, 0)[1].measure).toBe("eval:tone");
    expect(toggleFull(cards, 0)[0].full).toBe(true);
    expect(removeCard(cards, 0).map((c) => c.measure)).toEqual(["cost"]);
    expect(moveCard(cards, 0, 1).map((c) => c.measure)).toEqual([
      "cost",
      "eval:tone",
    ]);
    expect(moveCard(cards, 0, 5)).toBe(cards);
  });
  it("a full board refuses new cards", () => {
    const full = parseBoard(
      Array.from({ length: BOARD_MAX }, () => "cost~bars~none~1").join("|"),
    );
    expect(addCard(full, A)).toBe(full);
    expect(duplicateCard(full, 0)).toBe(full);
  });
  it("titles", () => {
    expect(
      cardTitle(A, { measureLabel: "Tone", groupLabel: "Difficulty" }),
    ).toBe("Tone by difficulty");
    expect(
      cardTitle(B, { measureLabel: "Cost per attempt", groupLabel: "None" }),
    ).toBe("Cost per attempt by run");
  });
});

// Review M4: the board offers every chart kind of the pivot's picker
// (WorkbenchRail CHARTS: the existing 5 plus B5's 2), each round-trips.
describe("every pivot chart kind is a board kind", () => {
  it("BOARD_CHARTS = the pivot's chart picker", async () => {
    const { CHARTS } = await import("../WorkbenchRail");
    expect(BOARD_CHARTS.map((c) => c.key).sort()).toEqual(
      CHARTS.map((c) => c.key).sort(),
    );
    expect(BOARD_CHARTS.map((c) => c.label)).toEqual(
      expect.arrayContaining(["Trend", "Delta bars"]),
    );
  });
  it("Trend and Delta bars round-trip in the URL", () => {
    const trend = { measure: "eval:tone", chart: "trend", rows: "none" };
    const delta = {
      measure: ALL_METRICS,
      chart: "delta_bars",
      rows: "none",
      full: true,
    };
    const s = serializeBoard([trend, delta]);
    expect(s).toBe("eval:tone~trend~none~1|all~delta_bars~none~2");
    const back = parseBoard(s).map(({ measure, chart, rows, full }) => ({
      measure,
      chart,
      rows,
      full,
    }));
    expect(back).toEqual([{ ...trend, full: false }, delta]);
    expect(addCard([], trend)).toHaveLength(1);
    expect(addCard([], delta)).toHaveLength(1);
  });
  it("a card's title names its kind, so two kinds on one measure differ", () => {
    const at = (chart, rows = "none") =>
      cardTitle(
        { measure: "eval:g", chart, rows },
        { measureLabel: "Groundedness", groupLabel: "Difficulty" },
      );
    expect(at("bars")).toBe("Groundedness by run");
    expect(at("trend")).toBe("Trend · Groundedness");
    expect(at("scatter2")).toBe("Two runs (scatter) · Groundedness");
    expect(at("cost_quality")).toBe("Cost vs quality · Groundedness");
    expect(at("heatmap", "meta:difficulty")).toBe(
      "Heatmap · Groundedness by difficulty",
    );
    expect(at("delta_bars")).toBe("Delta bars · every metric vs the baseline");
    const titles = BOARD_CHARTS.map((c) => at(c.key));
    expect(new Set(titles).size).toBe(titles.length);
  });
});
