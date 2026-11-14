import { describe, expect, it } from "vitest";
import {
  evidenceRanges,
  splitByRanges,
  unplacedEvidence,
} from "../insightsEvidence";
import unitsFixture from "./fixtures/run_sessions_failures_units_enriched.json";

const content =
  "Customer: Where is my order HP-72336?\nHarbor: Could you share your order number?\nCustomer: I just gave it to you.";

describe("evidenceRanges", () => {
  it("uses offsets that match the quote", () => {
    const start = content.indexOf("Could you");
    const quote = "Could you share your order number?";
    expect(
      evidenceRanges(content, [{ quote, start, end: start + quote.length }]),
    ).toEqual([{ start, end: start + quote.length, index: 0 }]);
  });

  it("finds the quote when the offsets are missing or wrong", () => {
    const at = content.indexOf("I just gave it");
    expect(
      evidenceRanges(content, [
        { quote: "I just gave it to you.", start: 3, end: 9 },
      ]),
    ).toEqual([
      { start: at, end: at + "I just gave it to you.".length, index: 0 },
    ]);
    expect(evidenceRanges(content, [{ quote: "where is my ORDER" }])).toEqual([
      { start: 10, end: 27, index: 0 },
    ]);
  });

  it("matches across different whitespace", () => {
    const [range] = evidenceRanges(content, [
      { quote: "HP-72336?   Harbor:   Could" },
    ]);
    expect(content.slice(range.start, range.end)).toBe(
      "HP-72336?\nHarbor: Could",
    );
  });

  it("merges overlapping quotes and skips ones it cannot place", () => {
    expect(
      evidenceRanges(content, [
        { quote: "share your order" },
        { quote: "Could you share" },
        { quote: "not in the text" },
      ]),
    ).toEqual([
      {
        start: content.indexOf("Could you"),
        end: content.indexOf("share your order") + "share your order".length,
        index: 0,
      },
    ]);
    expect(evidenceRanges("", [{ quote: "x" }])).toEqual([]);
    expect(evidenceRanges(content, null)).toEqual([]);
  });

  it("places every quote of the fixtures' units at its offsets", () => {
    unitsFixture.result.cited.forEach((unit) => {
      const ranges = evidenceRanges(unit.content, unit.facets.evidence);
      expect(ranges).toHaveLength(unit.facets.evidence.length);
      ranges.forEach((range) =>
        expect(unit.content.slice(range.start, range.end)).toBe(
          unit.facets.evidence[range.index].quote,
        ),
      );
    });
  });
});

describe("splitByRanges", () => {
  it("splits content into plain and marked segments", () => {
    expect(
      splitByRanges("abcdef", [
        { start: 1, end: 2, index: 0 },
        { start: 4, end: 6, index: 1 },
      ]),
    ).toEqual([
      { text: "a", mark: false },
      { text: "b", mark: true, index: 0 },
      { text: "cd", mark: false },
      { text: "ef", mark: true, index: 1 },
    ]);
    expect(splitByRanges("abc", [])).toEqual([{ text: "abc", mark: false }]);
  });
});

describe("unplacedEvidence", () => {
  it("returns the quotes that are not in the content", () => {
    expect(
      unplacedEvidence(content, [
        { quote: "Could you share" },
        { quote: "missing quote" },
        { quote: "" },
      ]),
    ).toEqual([{ quote: "missing quote" }]);
  });
});
