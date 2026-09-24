import { describe, it, expect } from "vitest";
import {
  PRD_AXES,
  FORCED_O_CELLS,
  perAxisCoverage,
  pairwiseCoverage,
  forcedCellsCoverage,
  overlayOf,
} from "../_fixtures/coverage";

// A scenario whose situation text carries one adversarial overlay keyword, so
// overlayOf resolves to that O-axis level deterministically.
const withOverlay = (overlay) => ({
  id: `s-${overlay}`,
  name: overlay,
  title: overlay,
  task: "",
  situation: overlay,
  turns: 1,
  persona: { age: 40 },
});

const env = { surface: "chat" };

describe("overlayOf", () => {
  it("maps each forced-cell keyword to its overlay", () => {
    FORCED_O_CELLS.forEach((id) => {
      expect(overlayOf(withOverlay(id))).toBe(id);
    });
  });

  it("defaults to none when nothing adversarial is present", () => {
    expect(overlayOf({ id: "s-plain", title: "Look up an order", situation: "calm" })).toBe("none");
  });
});

describe("forcedCellsCoverage", () => {
  it("reports every forced overlay present when the suite hits all five", () => {
    const suite = FORCED_O_CELLS.map(withOverlay);
    const forced = forcedCellsCoverage(suite);
    expect(forced).toHaveLength(FORCED_O_CELLS.length);
    expect(forced.every((f) => f.present)).toBe(true);
  });

  it("flags the missing ones when only some are present", () => {
    const forced = forcedCellsCoverage([withOverlay("destructive")]);
    expect(forced.filter((f) => f.present).map((f) => f.id)).toEqual(["destructive"]);
    expect(forced.filter((f) => !f.present)).toHaveLength(FORCED_O_CELLS.length - 1);
  });
});

describe("perAxisCoverage", () => {
  it("returns one entry per PRD axis with a 0..1 ratio", () => {
    const per = perAxisCoverage(FORCED_O_CELLS.map(withOverlay), env);
    expect(per.map((a) => a.id)).toEqual(PRD_AXES.map((a) => a.id));
    per.forEach((a) => {
      expect(a.ratio).toBeGreaterThanOrEqual(0);
      expect(a.ratio).toBeLessThanOrEqual(1);
      expect(a.hit + a.missingLevels.length).toBe(a.total);
    });
  });

  it("counts distinct O-axis levels the suite covers", () => {
    // Five distinct forced overlays → five distinct O levels hit.
    const o = perAxisCoverage(FORCED_O_CELLS.map(withOverlay), env).find((a) => a.id === "O");
    expect(o.hit).toBe(FORCED_O_CELLS.length);
  });
});

describe("pairwiseCoverage", () => {
  it("returns one entry per unordered axis pair with a 0..1 ratio", () => {
    const pairs = pairwiseCoverage(FORCED_O_CELLS.map(withOverlay), env);
    // C(6,2) = 15 unordered pairs of the six axes.
    expect(pairs).toHaveLength(15);
    pairs.forEach((p) => {
      expect(p.ratio).toBeGreaterThanOrEqual(0);
      expect(p.ratio).toBeLessThanOrEqual(1);
      expect(p.filled).toBeLessThanOrEqual(p.total);
    });
  });
});
