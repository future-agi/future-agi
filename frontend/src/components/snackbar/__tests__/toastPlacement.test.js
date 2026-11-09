import { describe, expect, it } from "vitest";
import { measureToastPlacement, measureToastRoom } from "../toastPlacement";

// A tiny DOM stub: `<main>` at x=200 and, optionally, one fixed bar.
function stubDom({ bar } = {}) {
  const main = { getBoundingClientRect: () => ({ left: 200 }) };
  const barEl = bar
    ? {
        parentElement: null,
        closest: () => null,
        getBoundingClientRect: () => bar,
      }
    : null;
  const doc = {
    body: {},
    documentElement: {},
    getElementById: (id) => (id === "main-content" ? main : null),
    querySelector: () => null,
    elementsFromPoint: (x, y) =>
      barEl &&
      x >= bar.left &&
      x <= bar.right &&
      y >= bar.top &&
      y <= bar.bottom
        ? [barEl]
        : [],
  };
  const view = {
    innerHeight: 720,
    getComputedStyle: () => ({ position: "fixed" }),
  };
  return { doc, view };
}

describe("toast placement (IT-R1 G2 P8)", () => {
  it("sits 16 px right of the page content's left edge", () => {
    const { doc, view } = stubDom();
    expect(measureToastPlacement(doc, view)).toEqual({ left: 216, lift: 0 });
  });
  it("lifts over a fixed bar at the bottom that overlaps the toast column", () => {
    const { doc, view } = stubDom({
      bar: {
        left: 228,
        right: 1252,
        top: 606,
        bottom: 650,
        height: 44,
        width: 1024,
      },
    });
    // 720 − 606 + 8
    expect(measureToastPlacement(doc, view).lift).toBe(122);
  });
  it("ignores a bar outside the toast column", () => {
    const { doc, view } = stubDom({
      bar: {
        left: 900,
        right: 1200,
        top: 606,
        bottom: 650,
        height: 44,
        width: 300,
      },
    });
    expect(measureToastPlacement(doc, view).lift).toBe(0);
  });
});

describe("toast room next to drawers and dialogs (IT-R2 G2 P10)", () => {
  const view = { innerHeight: 720, getComputedStyle: () => ({}) };
  const docWith = (rects) => ({
    querySelectorAll: () =>
      rects.map((r) => ({
        closest: () => null,
        getBoundingClientRect: () => r,
      })),
  });
  it("is free without a drawer", () => {
    expect(measureToastRoom({ left: 216, lift: 0 }, docWith([]), view)).toEqual(
      {
        room: Infinity,
        hidden: false,
      },
    );
  });
  it("hides the toast when a right drawer leaves less than 360 px", () => {
    // The New experiment drawer at 1280×720 starts at x=560.
    const drawer = {
      left: 560,
      right: 1280,
      top: 0,
      bottom: 720,
      width: 720,
      height: 720,
    };
    const r = measureToastRoom({ left: 216, lift: 0 }, docWith([drawer]), view);
    expect(r.room).toBe(560 - 16 - 216);
    expect(r.hidden).toBe(true);
  });
  it("narrows the column next to a narrower drawer", () => {
    const drawer = {
      left: 900,
      right: 1440,
      top: 0,
      bottom: 900,
      width: 540,
      height: 900,
    };
    const r = measureToastRoom({ left: 216, lift: 0 }, docWith([drawer]), {
      ...view,
      innerHeight: 900,
    });
    expect(r).toEqual({ room: 900 - 16 - 216, hidden: false });
  });
  it("ignores a dialog above the toast column", () => {
    const dialog = {
      left: 300,
      right: 900,
      top: 100,
      bottom: 400,
      width: 600,
      height: 300,
    };
    expect(
      measureToastRoom({ left: 216, lift: 0 }, docWith([dialog]), view).hidden,
    ).toBe(false);
  });
});
