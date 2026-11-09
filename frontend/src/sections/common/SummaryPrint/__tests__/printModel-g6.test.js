// L7c g6 (print:view): money keeps 2 significant figures under a cent; a
// verdict card's words are printed once.
import { describe, expect, it } from "vitest";
import { fmtMetric, printCardLine } from "../printModel";

describe("print money (L7c g6)", () => {
  it("never prints $0.0000 for a real sub-cent cost", () => {
    expect(fmtMetric({ key: "ops:cost", family: "ops" }, 0.00005)).toBe(
      "$0.000050",
    );
    expect(fmtMetric({ key: "ops:cost", family: "ops" }, 0.0021)).toMatch(
      /^\$0\.0021$/,
    );
  });
});

describe("printCardLine (L7c g6 print:view)", () => {
  it("drops the chip label when the sentence already opens with it", () => {
    const line = printCardLine({
      tone: "tie",
      deltaPts: -2.9,
      chips: [],
      sentence:
        "Too close to call: #2A Support Reply V4 vs baseline #1A, −2.9 pts success",
    });
    expect(line).toBe(
      "Too close to call: #2A Support Reply V4 vs baseline #1A, −2.9 pts success",
    );
    expect(line).not.toMatch(/Too close to call — Too close to call/);
  });
  it("keeps label — sentence when the sentence does not repeat it", () => {
    const line = printCardLine({
      tone: "better",
      deltaPts: 25.5,
      chips: [{ key: "success", label: "+25.5 pts", tone: "good" }],
      sentence: "#2B beats baseline #2D on Task success",
    });
    expect(line).toMatch(/^Better .+ — #2B beats baseline #2D/);
  });
});
