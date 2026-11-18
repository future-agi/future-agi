import { describe, expect, it } from "vitest";
import { buildRefIndex, parseCitations } from "../insightsCitations";
import runFixture from "./fixtures/run_sessions_failures.json";

const summary = runFixture.result.result.summary;

describe("buildRefIndex", () => {
  it("maps every example number to its unit and category", () => {
    const index = buildRefIndex(summary);
    expect(index.size).toBe(16);
    expect(index.get(3)).toEqual({
      unitId: "910105df-fc0e-5444-84e2-9f14361b21ff",
      categoryId: "21d2b5dfc84e",
    });
  });

  it("takes refs that only appear in key points or highlights", () => {
    const index = buildRefIndex({
      key_points: [{ text: "x (#2)", refs: [{ ref: 2, unit_id: "u2" }] }],
      highlights: [{ ref: 5, unit_id: "u5", category_id: "c5" }],
    });
    expect(index.get(2)).toEqual({ unitId: "u2", categoryId: "" });
    expect(index.get(5)).toEqual({ unitId: "u5", categoryId: "c5" });
  });

  it("is empty for a summary that failed", () => {
    expect(buildRefIndex({ error: "unavailable" }).size).toBe(0);
    expect(buildRefIndex(undefined).size).toBe(0);
  });
});

describe("parseCitations", () => {
  const index = new Map([
    [1, { unitId: "a" }],
    [2, { unitId: "b" }],
    [7, { unitId: "c" }],
  ]);

  it("turns a parenthesised group into one refs segment without the brackets", () => {
    expect(parseCitations("Harbor loops (#1, #2). Fix it.", index)).toEqual([
      { type: "text", text: "Harbor loops " },
      { type: "refs", refs: [1, 2] },
      { type: "text", text: ". Fix it." },
    ]);
  });

  it("reads a lone citation and 'and'-joined groups", () => {
    expect(parseCitations("see #7 and (#1 and #2)", index)).toEqual([
      { type: "text", text: "see " },
      { type: "refs", refs: [7] },
      { type: "text", text: " and " },
      { type: "refs", refs: [1, 2] },
    ]);
  });

  it("leaves unknown numbers as plain text", () => {
    expect(parseCitations("order #12345 (#1, #9)", index)).toEqual([
      { type: "text", text: "order #12345 (#1, #9)" },
    ]);
  });

  it("parses the real summary so every cited number becomes a chip", () => {
    const refIndex = buildRefIndex(summary);
    summary.key_points.forEach((point) => {
      const cited = parseCitations(point.text, refIndex)
        .filter((s) => s.type === "refs")
        .flatMap((s) => s.refs);
      expect(cited.sort((a, b) => a - b)).toEqual(
        point.refs.map((r) => r.ref).sort((a, b) => a - b),
      );
      const text = parseCitations(point.text, refIndex)
        .filter((s) => s.type === "text")
        .map((s) => s.text)
        .join("");
      expect(text).not.toMatch(/#\d/);
    });
  });
});
