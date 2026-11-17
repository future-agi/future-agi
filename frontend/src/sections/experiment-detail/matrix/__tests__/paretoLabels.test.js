import { describe, expect, it } from "vitest";
import { placeLabels } from "../paretoLabels";
import { logTicks } from "../ParetoStrip";

const overlap = (a, b) =>
  a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

describe("placeLabels", () => {
  it("keeps the default slot (up-right) when nothing collides", () => {
    const out = placeLabels([{ id: "a", x: 100, y: 100, label: "#2A" }], {
      left: 0,
      right: 400,
      top: 0,
      bottom: 200,
    });
    expect(out.a).toMatchObject({ x: 109, y: 93, anchor: "start" });
    expect(out.a.leader).toBeNull();
  });
  it("moves a crowded label (#2E next to #2D) and never overlaps", () => {
    const pts = [
      { id: "e", x: 300, y: 40, label: "#2E" },
      { id: "d", x: 318, y: 36, label: "#2D" },
      { id: "c", x: 200, y: 50, label: "#2C" },
      { id: "b", x: 206, y: 58, label: "#2B" },
    ];
    const out = placeLabels(pts, { left: 0, right: 400, top: 10, bottom: 180 });
    const boxes = Object.values(out).map((o) => o.box);
    for (let i = 0; i < boxes.length; i += 1)
      for (let j = i + 1; j < boxes.length; j += 1)
        expect(overlap(boxes[i], boxes[j])).toBe(false);
  });
  it("flips to the left at the right edge", () => {
    const out = placeLabels([{ id: "d", x: 398, y: 100, label: "#2D" }], {
      left: 0,
      right: 400,
      top: 0,
      bottom: 200,
    });
    expect(out.d.anchor).toBe("end");
  });
});

describe("IT-R2 G2 P12: labels never overlap; at least 5 ticks", () => {
  it("hides a label that has no free slot instead of overlapping", () => {
    // Two points on the same pixel: the second label has nowhere to go.
    const pts = [
      { id: "f", x: 200, y: 100, label: "#2F Support Agent v3.1 (draft)" },
      { id: "g", x: 200, y: 101, label: "#2G Support Agent v3.1 (draft)" },
      { id: "h", x: 201, y: 100, label: "#2H Support Agent v3.1 (draft)" },
      { id: "i", x: 199, y: 102, label: "#2I Support Agent v3.1 (draft)" },
      { id: "j", x: 200, y: 99, label: "#2J Support Agent v3.1 (draft)" },
      { id: "k", x: 202, y: 100, label: "#2K Support Agent v3.1 (draft)" },
    ];
    const out = placeLabels(pts, { left: 0, right: 420, top: 40, bottom: 160 });
    const boxes = Object.values(out)
      .filter(Boolean)
      .map((l) => l.box);
    for (let i = 0; i < boxes.length; i += 1)
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        expect(a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1).toBe(
          false,
        );
      }
    expect(Object.values(out).some((l) => l === null)).toBe(true);
  });
  it("gives a 0.004–0.24 cost axis at least 5 ticks", () => {
    const t = logTicks(0.004, 0.24, 5);
    expect(t.length).toBeGreaterThanOrEqual(5);
    expect(t.length).toBeLessThanOrEqual(5);
  });
});

// IT-R4 G6 P9: short names on the chart points.
import { shortPointName } from "../paretoLabels";
describe("shortPointName", () => {
  it("names a version or the first words of the run", () => {
    expect(shortPointName("#2F", { version: "v3.1 (draft)" })).toBe("#2F v3.1");
    expect(shortPointName("#2D", { name: "Claude Code" })).toBe(
      "#2D Claude Code",
    );
    expect(
      shortPointName("#2C", { name: "LangGraph support agent 3fa91c2" }),
    ).toBe("#2C LangGraph");
  });
});
