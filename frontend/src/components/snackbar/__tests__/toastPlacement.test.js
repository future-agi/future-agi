import { describe, expect, it } from "vitest";
import {
  measureToastPlacement,
  measureToastRoom,
  MIN_ROOM_PX,
} from "../toastPlacement";

// A tiny DOM stub: a 1280×720 window, `<main>` from x=200 to `mainRight`
// and, optionally, fixed boxes found by the hit test (`fixed`) or by their
// selector (`launchers`: the Falcon bubble, the run tray chip).
const asEl = (r) => ({
  parentElement: null,
  closest: () => null,
  getBoundingClientRect: () => r,
});
function stubDom({
  mainRight = 1280,
  clientWidth,
  fixed = [],
  launchers = [],
  hitTest = true,
} = {}) {
  const main = {
    clientWidth,
    clientLeft: 0,
    getBoundingClientRect: () => ({ left: 200, right: mainRight }),
  };
  const fixedEls = fixed.map(asEl);
  const launcherEls = launchers.map(asEl);
  const doc = {
    body: {},
    documentElement: { clientWidth: 1280 },
    getElementById: (id) => (id === "main-content" ? main : null),
    querySelector: () => null,
    querySelectorAll: (sel) =>
      sel.includes("MuiFab-root") ? launcherEls : [],
  };
  if (hitTest)
    doc.elementsFromPoint = (x, y) =>
      fixedEls.filter((el) => {
        const r = el.getBoundingClientRect();
        return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
      });
  const view = {
    innerWidth: 1280,
    innerHeight: 720,
    getComputedStyle: () => ({ position: "fixed" }),
  };
  return { doc, view };
}

// FalconAIFab.jsx: fixed, bottom 72, right 12, 40×40 → at 1280×720.
const FALCON_BUBBLE = {
  left: 1228,
  right: 1268,
  top: 608,
  bottom: 648,
  width: 40,
  height: 40,
};

describe("toast placement, bottom-right", () => {
  it("sits 16 px inside the page content's right edge", () => {
    const { doc, view } = stubDom();
    expect(measureToastPlacement(doc, view)).toEqual({
      right: 16,
      lift: 0,
      edge: 1264,
      start: 216,
    });
  });
  it("falls back to the window edge when <main> has no size", () => {
    const { doc, view } = stubDom();
    doc.getElementById = () => ({
      clientWidth: 0,
      clientLeft: 0,
      getBoundingClientRect: () => ({ left: 0, right: 0, width: 0, height: 0 }),
    });
    const place = measureToastPlacement(doc, view);
    expect(place.edge).toBe(1280 - 16);
    expect(place.right).toBe(16);
  });
  it("writes the inset from the window's right edge, not the edge's x", () => {
    // Content ends at x=1000 (e.g. a docked panel right of <main>).
    const { doc, view } = stubDom({ mainRight: 1000 });
    const place = measureToastPlacement(doc, view);
    expect(place.edge).toBe(1000 - 16);
    expect(place.right).toBe(1280 - (1000 - 16));
  });
  it("keeps the gap from the content, not from <main>'s scrollbar", () => {
    // <main> is 200..1280 with a 15 px scrollbar: content ends at 1265.
    const { doc, view } = stubDom({ clientWidth: 1065 });
    const place = measureToastPlacement(doc, view);
    expect(place.edge).toBe(1265 - 16);
    expect(place.right).toBe(1280 - 1249);
  });
  it("lifts over a fixed bar at the bottom that overlaps the toast column", () => {
    const { doc, view } = stubDom({
      fixed: [
        {
          left: 228,
          right: 1252,
          top: 606,
          bottom: 650,
          height: 44,
          width: 1024,
        },
      ],
    });
    // 720 − 606 + 8
    expect(measureToastPlacement(doc, view).lift).toBe(122);
  });
  it("ignores a bar outside the toast column (the old left column)", () => {
    const { doc, view } = stubDom({
      fixed: [
        {
          left: 228,
          right: 600,
          top: 606,
          bottom: 650,
          height: 44,
          width: 372,
        },
      ],
    });
    expect(measureToastPlacement(doc, view).lift).toBe(0);
  });
  it("lifts over the Falcon bubble found by the hit test", () => {
    const { doc, view } = stubDom({ fixed: [FALCON_BUBBLE] });
    // 720 − 608 + 8
    expect(measureToastPlacement(doc, view).lift).toBe(120);
  });
  it("lifts over the Falcon bubble by its box when the hit test misses it", () => {
    const { doc, view } = stubDom({
      launchers: [FALCON_BUBBLE],
      hitTest: false,
    });
    expect(measureToastPlacement(doc, view).lift).toBe(120);
  });
  it("lifts over the run tray chip left of the bubble", () => {
    // RunTray.jsx: fixed, bottom 16, right 72, 36 px tall.
    const chip = {
      left: 1000,
      right: 1208,
      top: 668,
      bottom: 704,
      width: 208,
      height: 36,
    };
    const { doc, view } = stubDom({ launchers: [chip], hitTest: false });
    // 720 − 668 + 8
    expect(measureToastPlacement(doc, view).lift).toBe(60);
  });
  it("does not lift for a bubble right of a column that ends before it", () => {
    const { doc, view } = stubDom({
      mainRight: 1000,
      launchers: [FALCON_BUBBLE],
      hitTest: false,
    });
    expect(measureToastPlacement(doc, view).lift).toBe(0);
  });
});

describe("toast room next to drawers and dialogs", () => {
  const view = {
    innerWidth: 1280,
    innerHeight: 720,
    getComputedStyle: () => ({}),
  };
  // measureToastPlacement() at 1280×720 with <main> at 200..1280.
  const place = { right: 16, lift: 0, edge: 1264, start: 216 };
  const docWith = (rects) => ({
    querySelectorAll: () =>
      rects.map((r) => ({
        closest: () => null,
        getBoundingClientRect: () => r,
      })),
  });
  it("moves left of a custom right panel marked data-fa-overlay-panel", () => {
    const seen = [];
    const doc = {
      querySelectorAll: (sel) => {
        seen.push(sel);
        return [
          {
            closest: () => null,
            getBoundingClientRect: () => ({
              left: 760,
              right: 1280,
              top: 120,
              bottom: 720,
              width: 520,
              height: 600,
            }),
          },
        ];
      },
    };
    const r = measureToastRoom(place, doc, view);
    expect(seen.join(" ")).toContain("[data-fa-overlay-panel]");
    expect(r.edge).toBe(760 - 16);
    expect(r.hidden).toBe(false);
  });
  const panelEl = (r) => ({
    kind: "[data-fa-overlay-panel]",
    closest: () => null,
    matches: (sel) => sel === "[data-fa-overlay-panel]",
    getBoundingClientRect: () => r,
  });
  const drawerEl = (r) => ({
    kind: ".MuiDrawer-paper",
    closest: () => null,
    matches: (sel) => sel.includes(".MuiDrawer-paper"),
    getBoundingClientRect: () => r,
  });
  // Returns only what the selector asks for, as the DOM does.
  const docOf = (els) => ({
    querySelectorAll: (sel) => els.filter((el) => sel.includes(el.kind)),
  });
  // The result panel in full mode spans the whole content (200..1280 here).
  const FULL_PANEL = {
    left: 200,
    right: 1280,
    top: 56,
    bottom: 720,
    width: 1080,
    height: 664,
  };
  it("never hides the toasts for a full-width custom panel: the column stays where it is", () => {
    expect(measureToastRoom(place, docOf([panelEl(FULL_PANEL)]), view)).toEqual(
      { room: Infinity, hidden: false, right: 16, edge: 1264 },
    );
  });
  it("moves left of a custom panel while 360 px or more is left beside it", () => {
    const panel = {
      left: 700,
      right: 1280,
      top: 56,
      bottom: 720,
      width: 580,
      height: 664,
    };
    expect(measureToastRoom(place, docOf([panelEl(panel)]), view)).toEqual({
      room: 700 - 16 - 216,
      hidden: false,
      right: 1280 - (700 - 16),
      edge: 700 - 16,
    });
  });
  it("still places the column by a drawer over a full-width panel, over the drawer when the room is short", () => {
    const drawer = {
      left: 560,
      right: 1280,
      top: 0,
      bottom: 720,
      width: 720,
      height: 720,
    };
    const r = measureToastRoom(
      place,
      docOf([panelEl(FULL_PANEL), drawerEl(drawer)]),
      view,
    );
    // The room beside the drawer is short: the column shows over the
    // drawer, where it sits with nothing open.
    expect(r).toEqual({
      room: 560 - 16 - 216,
      hidden: false,
      right: 16,
      edge: 1264,
      over: true,
      width: 420,
      lift: 0,
    });
  });
  it("is free without a drawer", () => {
    expect(measureToastRoom(place, docWith([]), view)).toEqual({
      room: Infinity,
      hidden: false,
      right: 16,
      edge: 1264,
    });
  });
  it("shows the column over a right drawer when the room left beside it is under 360 px", () => {
    // The New experiment drawer at 1280×720 starts at x=560.
    const drawer = {
      left: 560,
      right: 1280,
      top: 0,
      bottom: 720,
      width: 720,
      height: 720,
    };
    const r = measureToastRoom(place, docWith([drawer]), view);
    expect(r.room).toBe(560 - 16 - 216);
    expect(r.room).toBeLessThan(MIN_ROOM_PX);
    expect(r.hidden).toBe(false);
    expect(r.over).toBe(true);
    // 16 px inside the drawer's (and the window's) right edge, 420 px wide.
    expect(r.edge).toBe(1280 - 16);
    expect(r.right).toBe(16);
    expect(r.width).toBe(420);
  });
  it("moves the column left of a narrower drawer, its right edge 16 px from it", () => {
    const drawer = {
      left: 900,
      right: 1440,
      top: 0,
      bottom: 900,
      width: 540,
      height: 900,
    };
    const r = measureToastRoom(
      { right: 16, lift: 0, edge: 1424, start: 216 },
      docWith([drawer]),
      { ...view, innerWidth: 1440, innerHeight: 900 },
    );
    expect(r).toEqual({
      room: 900 - 16 - 216,
      hidden: false,
      right: 1440 - (900 - 16),
      edge: 900 - 16,
    });
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
    expect(measureToastRoom(place, docWith([dialog]), view)).toMatchObject({
      room: Infinity,
      hidden: false,
      right: 16,
    });
  });
  it("ignores the left nav and a tall dialog that end left of the column", () => {
    const nav = {
      left: 0,
      right: 200,
      top: 0,
      bottom: 720,
      width: 200,
      height: 720,
    };
    // The column spans 844..1264.
    const dialog = {
      left: 300,
      right: 800,
      top: 40,
      bottom: 680,
      width: 500,
      height: 640,
    };
    const r = measureToastRoom(place, docWith([nav, dialog]), view);
    expect(r).toMatchObject({ room: Infinity, hidden: false, right: 16 });
  });
  it("moves past a dialog over a right drawer too, and shows the column over the dialog when no room is left", () => {
    const drawer = {
      left: 860,
      right: 1280,
      top: 0,
      bottom: 720,
      width: 420,
      height: 720,
    };
    const dialog = {
      left: 400,
      right: 880,
      top: 200,
      bottom: 700,
      width: 480,
      height: 500,
    };
    const r = measureToastRoom(place, docWith([dialog, drawer]), view);
    expect(r.room).toBe(400 - 16 - 216);
    // Over the dialog: 16 px inside its right edge, 420 px wide.
    expect(r).toEqual({
      room: 400 - 16 - 216,
      hidden: false,
      right: 1280 - (880 - 16),
      edge: 880 - 16,
      over: true,
      width: 420,
      lift: 0,
    });
  });
});

// A whole screen at one size, read the way the app reads it: the side nav is
// 200 px wide, so <main> runs from x=200 to the window's right edge, or to
// `mainRight` when a sidebar is docked right of it. `els` are the open
// drawers, dialogs and panels; each answers querySelectorAll with the boxes
// inside it (its buttons, an actions bar, a marked footer), and the document
// answers with every box, filtered by the selector as the DOM does.
const rect = (left, top, right, bottom) => ({
  left,
  top,
  right,
  bottom,
  width: right - left,
  height: bottom - top,
});
const box = (kind, r) => ({
  kind,
  parentElement: null,
  closest: () => null,
  matches: (sel) => sel.includes(kind),
  getBoundingClientRect: () => r,
});
const holder = (kind, r, inner = []) => ({
  ...box(kind, r),
  inner,
  querySelectorAll: (sel) => inner.filter((el) => sel.includes(el.kind)),
});
function screenAt(vw, vh, els = [], { mainRight = vw } = {}) {
  const all = els.flatMap((el) => [el, ...el.inner]);
  const main = {
    clientLeft: 0,
    getBoundingClientRect: () => rect(200, 0, mainRight, vh),
  };
  const doc = {
    body: {},
    documentElement: { clientWidth: vw },
    getElementById: (id) => (id === "main-content" ? main : null),
    querySelector: () => null,
    querySelectorAll: (sel) => all.filter((el) => sel.includes(el.kind)),
  };
  const view = {
    innerWidth: vw,
    innerHeight: vh,
    getComputedStyle: () => ({}),
  };
  return { doc, view };
}
const measureAt = (vw, vh, els, layout) => {
  const { doc, view } = screenAt(vw, vh, els, layout);
  return measureToastRoom(measureToastPlacement(doc, view), doc, view);
};
/** How tall the toast column is taken to be (two toasts). */
const COLUMN_PX = 140;

// The Create Label drawer (annotations/labels/create-label-drawer.jsx): a
// right drawer, width 60vw with a 960 px minimum, so 960 px wide at 1440,
// 1280 and 1024. Its footer (16 px padding top and bottom, 24 px at the
// sides) holds Cancel and Create, 160 px wide and 32 px tall, 16 px apart.
const createLabelDrawer = (vw, vh) => {
  const left = vw - Math.max(960, Math.round(vw * 0.6));
  const top = vh - 16 - 32;
  return holder(".MuiDrawer-paper", rect(left, 0, vw, vh), [
    box("button", rect(vw - 360, top, vw - 200, top + 32)),
    box("button", rect(vw - 184, top, vw - 24, top + 32)),
  ]);
};
// A tall centred dialog (maxWidth "md" with fullWidth: 900 px wide; MUI's
// maximum height, the window − 64 px). Its actions bar (12 px / 20 px
// padding, so 56 px tall) holds Cancel and Save, 88 px wide, 8 px apart.
const centredDialog = (vw, vh) => {
  const left = Math.round((vw - 900) / 2);
  const right = left + 900;
  const top = vh - 32 - 56;
  return holder(".MuiDialog-paper", rect(left, 32, right, vh - 32), [
    box(".MuiDialogActions-root", rect(left, top, right, vh - 32)),
    box("button", rect(right - 204, top + 12, right - 116, top + 44)),
    box("button", rect(right - 108, top + 12, right - 20, top + 44)),
  ]);
};
// A drawer as wide as the window, with a 64 px footer marked
// data-toast-clear and a Save button in it.
const fullWidthDrawer = (vw, vh) => {
  const footer = rect(0, vh - 64, vw, vh);
  return holder(".MuiDrawer-paper", rect(0, 0, vw, vh), [
    box("[data-toast-clear]", footer),
    box("button", rect(vw - 144, vh - 48, vw - 24, vh - 16)),
  ]);
};

/**
 * What every placement over a drawer or dialog must hold: shown, wide enough
 * to read, inside the window, inside the obstacle, clear of the obstacle's
 * own buttons and bars, and never lifted out of the window.
 */
function expectShownOver(r, obstacle, vw, vh) {
  expect(r.hidden).toBe(false);
  const o = obstacle.getBoundingClientRect();
  const colLeft = r.edge - r.width;
  // Inside the window.
  expect(r.right).toBe(vw - r.edge);
  expect(r.edge).toBeLessThanOrEqual(vw - 16);
  expect(colLeft).toBeGreaterThanOrEqual(0);
  // Wide enough to read: 320 px, or the obstacle's width less 32 px when the
  // obstacle is narrower than 352 px; never under 64 px.
  const shown = Math.min(vw, o.right) - Math.max(0, o.left);
  expect(r.width).toBeGreaterThanOrEqual(Math.min(320, shown - 32));
  expect(r.width).toBeGreaterThanOrEqual(64);
  // Inside the obstacle, 16 px in from its sides, never wider than 420 px.
  expect(r.width).toBeLessThanOrEqual(Math.min(420, o.width - 32));
  expect(r.edge).toBeLessThanOrEqual(Math.min(vw, o.right) - 16);
  expect(colLeft).toBeGreaterThanOrEqual(Math.max(0, o.left) + 16);
  // Clear of every button or bar under the column: 8 px above its top.
  obstacle.inner
    .map((el) => el.getBoundingClientRect())
    .filter((b) => b.right > colLeft && b.left < r.edge)
    .forEach((b) => expect(vh - r.lift).toBeLessThanOrEqual(b.top - 8));
  // Never lifted out of the window.
  expect(vh - r.lift - COLUMN_PX).toBeGreaterThanOrEqual(0);
}

describe("a toast over a drawer or dialog that leaves no room beside it", () => {
  const SIZES = [
    [1440, 900],
    [1280, 720],
    [1024, 768],
  ];
  // The column's CSS right inset over the tall centred dialog at each size.
  const DIALOG_INSETS = [
    [1440, 900, 1440 - (1170 - 16)],
    [1280, 720, 1280 - (1090 - 16)],
    [1024, 768, 1024 - (962 - 16)],
  ];

  SIZES.forEach(([vw, vh]) => {
    it(`shows the column over the Create Label drawer at ${vw}×${vh}, clear of Cancel and Create`, () => {
      const drawer = createLabelDrawer(vw, vh);
      const r = measureAt(vw, vh, [drawer]);
      expectShownOver(r, drawer, vw, vh);
      // The room beside the drawer is short, as before. The column sits over
      // the drawer where it sits with nothing open (16 px inside the window's
      // right edge, 420 px wide), lifted 8 px over the buttons.
      expect(r.room).toBe(vw - 960 - 16 - 216);
      expect(r.room).toBeLessThan(MIN_ROOM_PX);
      expect(r).toMatchObject({
        hidden: false,
        over: true,
        right: 16,
        edge: vw - 16,
        width: 420,
        lift: 16 + 32 + 8,
      });
    });

    it(`shows the column over a drawer as wide as the window at ${vw}×${vh}, above its marked footer`, () => {
      const drawer = fullWidthDrawer(vw, vh);
      const r = measureAt(vw, vh, [drawer]);
      expectShownOver(r, drawer, vw, vh);
      expect(r).toMatchObject({
        hidden: false,
        over: true,
        right: 16,
        edge: vw - 16,
        width: 420,
        lift: 64 + 8,
      });
    });
  });

  DIALOG_INSETS.forEach(([vw, vh, inset]) => {
    it(`shows the column inside a tall centred dialog at ${vw}×${vh}, above its actions`, () => {
      const dialog = centredDialog(vw, vh);
      const r = measureAt(vw, vh, [dialog]);
      expectShownOver(r, dialog, vw, vh);
      // Its right edge 16 px inside the dialog's; lifted 8 px over the
      // actions bar, which starts 32 + 56 px above the window's bottom.
      expect(r).toMatchObject({
        hidden: false,
        over: true,
        edge: vw - inset,
        right: inset,
        width: 420,
        lift: 32 + 56 + 8,
      });
    });
  });

  it("uses the dialog's own right edge when the page content ends too far left of it for a readable column", () => {
    // <main> runs from x=600 to x=1000; a tall dialog from x=700 to x=1400.
    const dialog = holder(".MuiDialog-paper", rect(700, 32, 1400, 868));
    const { doc, view } = screenAt(1440, 900, [dialog]);
    doc.getElementById = () => ({
      clientLeft: 0,
      getBoundingClientRect: () => rect(600, 0, 1000, 900),
    });
    const r = measureToastRoom(measureToastPlacement(doc, view), doc, view);
    expectShownOver(r, dialog, 1440, 900);
    // At the content's right edge (1000 − 16) the column would be only
    // 268 px wide, too narrow to read; it sits 16 px inside the dialog's
    // right edge instead (1400 − 16), 420 px wide.
    expect(r).toMatchObject({ over: true, edge: 1384, right: 56, width: 420 });
  });

  it("clears the buttons of a drawer under a dialog too", () => {
    // A tall dialog with no actions bar, opened over the Create Label drawer
    // at 1280×720: the drawer's Cancel button is under the column.
    const drawer = createLabelDrawer(1280, 720);
    const dialog = holder(".MuiDialog-paper", rect(190, 32, 1090, 688));
    const r = measureAt(1280, 720, [drawer, dialog]);
    expectShownOver(r, dialog, 1280, 720);
    const under = drawer.inner
      .map((el) => el.getBoundingClientRect())
      .filter((b) => b.right > r.edge - r.width && b.left < r.edge);
    expect(under.length).toBeGreaterThan(0);
    under.forEach((b) => expect(720 - r.lift).toBeLessThanOrEqual(b.top - 8));
  });

  it("never lifts the column out of a very short window", () => {
    // 1280×300: the drawer's button starts 175 px above the window's bottom.
    const drawer = holder(".MuiDrawer-paper", rect(320, 0, 1280, 300), [
      box("button", rect(1000, 125, 1200, 157)),
    ]);
    const r = measureAt(1280, 300, [drawer]);
    expect(r.hidden).toBe(false);
    expect(r.over).toBe(true);
    expect(r.lift).toBeGreaterThan(0);
    expect(300 - r.lift - COLUMN_PX).toBeGreaterThanOrEqual(0);
  });

  it("is never hidden, whatever is open", () => {
    SIZES.forEach(([vw, vh]) => {
      [
        [createLabelDrawer(vw, vh)],
        [centredDialog(vw, vh)],
        [fullWidthDrawer(vw, vh)],
        [createLabelDrawer(vw, vh), centredDialog(vw, vh)],
      ].forEach((els) => expect(measureAt(vw, vh, els).hidden).toBe(false));
    });
  });
});

// The assistant sidebar docked beside the page: a 420 px column right of
// <main> (at 900 px wide and up), so <main> ends 420 px before the window's
// right edge. A right drawer opened then covers the sidebar as well.
const DOCKED_SIDEBAR_PX = 420;
const docked = (vw) => ({ mainRight: vw - DOCKED_SIDEBAR_PX });
// A right drawer `w` px wide whose footer holds Cancel and Create, placed as
// in the Create Label drawer (160 × 32 px, 16 px above the window's bottom).
const rightDrawer = (vw, vh, w) => {
  const top = vh - 16 - 32;
  return holder(".MuiDrawer-paper", rect(vw - w, 0, vw, vh), [
    box("button", rect(vw - 360, top, vw - 200, top + 32)),
    box("button", rect(vw - 184, top, vw - 24, top + 32)),
  ]);
};
/** The column's width if its right edge stayed 16 px inside the content's. */
const widthAtContentEdge = (mainRight, obstacleLeft) =>
  Math.min(420, mainRight - 16 - obstacleLeft - 16);

describe("a toast over a drawer while the assistant sidebar is docked", () => {
  // Right drawers the app opens at these widths at 1024 px: 461 px (45vw),
  // 480, 560, 600, 640 and 720 px; and 440 px. <main> runs 200..604.
  const AT_1024 = [
    // [drawer width, column width]
    [440, 408],
    [461, 420],
    [480, 420],
    [560, 420],
    [600, 420],
    [640, 420],
    [720, 420],
  ];

  AT_1024.forEach(([w, width]) => {
    it(`shows a readable column over a ${w} px drawer at 1024×768, 16 px inside the drawer's right edge`, () => {
      const drawer = rightDrawer(1024, 768, w);
      const r = measureAt(1024, 768, [drawer], docked(1024));
      expectShownOver(r, drawer, 1024, 768);
      // At the content's edge (604 − 16) the column would be too narrow to
      // read, so it sits 16 px inside the drawer's right edge instead,
      // lifted 8 px over Cancel and Create.
      expect(widthAtContentEdge(604, 1024 - w)).toBeLessThan(320);
      expect(r.width).toBeGreaterThanOrEqual(Math.min(320, w - 32));
      expect(r).toMatchObject({
        hidden: false,
        over: true,
        right: 16,
        edge: 1024 - 16,
        width,
        lift: 16 + 32 + 8,
      });
    });
  });

  it("shows a readable column over a 720 px drawer at 1280×720, 16 px inside the drawer's right edge", () => {
    const drawer = rightDrawer(1280, 720, 720);
    const r = measureAt(1280, 720, [drawer], docked(1280));
    expectShownOver(r, drawer, 1280, 720);
    // <main> runs 200..860: at its edge the column would be 268 px wide.
    expect(widthAtContentEdge(860, 560)).toBe(268);
    expect(r).toMatchObject({
      hidden: false,
      over: true,
      right: 16,
      edge: 1280 - 16,
      width: 420,
      lift: 16 + 32 + 8,
    });
  });

  [
    [1440, 900],
    [1280, 720],
    [1024, 768],
  ].forEach(([vw, vh]) => {
    it(`keeps the column at the content's edge over the Create Label drawer at ${vw}×${vh}, where it is wide enough`, () => {
      const drawer = createLabelDrawer(vw, vh);
      const r = measureAt(vw, vh, [drawer], docked(vw));
      expectShownOver(r, drawer, vw, vh);
      // 420 px wide, where it sits with nothing open (left of the docked
      // sidebar), over the drawer; Cancel and Create are not under it.
      expect(r).toMatchObject({
        hidden: false,
        over: true,
        right: DOCKED_SIDEBAR_PX + 16,
        edge: vw - DOCKED_SIDEBAR_PX - 16,
        width: 420,
        lift: 0,
      });
    });
  });

  it("with the sidebar closed, keeps the column over the same drawers where it sits with nothing open", () => {
    AT_1024.forEach(([w]) => {
      const drawer = rightDrawer(1024, 768, w);
      const r = measureAt(1024, 768, [drawer]);
      expectShownOver(r, drawer, 1024, 768);
      expect(r).toMatchObject({
        hidden: false,
        over: true,
        right: 16,
        edge: 1024 - 16,
        width: Math.min(420, w - 32),
        lift: 16 + 32 + 8,
      });
    });
  });
});

describe("a drawer or dialog too narrow to hold a toast", () => {
  it("treats a 48 px dialog under the column as no obstacle", () => {
    // Sidebar docked at 1024×768: the column spans 168..588. A dialog 48 px
    // wide at 560..608 in the bottom band would leave no room beside it.
    const dialog = holder(".MuiDialog-paper", rect(560, 600, 608, 760));
    expect(measureAt(1024, 768, [dialog], docked(1024))).toEqual({
      room: Infinity,
      hidden: false,
      right: DOCKED_SIDEBAR_PX + 16,
      edge: 1024 - DOCKED_SIDEBAR_PX - 16,
    });
  });

  it("treats a 48 px drawer at the window's right edge as no obstacle", () => {
    const drawer = holder(".MuiDrawer-paper", rect(1232, 0, 1280, 720));
    expect(measureAt(1280, 720, [drawer])).toEqual({
      room: Infinity,
      hidden: false,
      right: 16,
      edge: 1264,
    });
  });
});

describe("a column over a drawer or dialog is never too narrow", () => {
  const SIZES = [
    [1440, 900],
    [1280, 720],
    [1024, 768],
  ];

  it("is never under 64 px wide, nor under a readable width, for any case above or any right drawer width", () => {
    const bad = [];
    SIZES.forEach(([vw, vh]) => {
      [undefined, docked(vw)].forEach((layout) => {
        const at = layout ? "docked" : "closed";
        const cases = [
          [createLabelDrawer(vw, vh)],
          [centredDialog(vw, vh)],
          [fullWidthDrawer(vw, vh)],
          [createLabelDrawer(vw, vh), centredDialog(vw, vh)],
        ];
        for (let w = 48; w <= vw; w += 1) cases.push([rightDrawer(vw, vh, w)]);
        cases.forEach((els) => {
          const boxes = els.map((el) => el.getBoundingClientRect());
          const name = `${vw}×${vh} ${at} ${boxes
            .map((b) => `${b.left}..${b.right}`)
            .join(" + ")}`;
          const r = measureAt(vw, vh, els, layout);
          if (r.hidden !== false) bad.push(`${name}: hidden`);
          if (!r.over) return;
          // The narrowest obstacle open sets the least readable width.
          const shown = Math.min(
            ...boxes.map((b) => Math.min(vw, b.right) - Math.max(0, b.left)),
          );
          if (!(r.width >= 64)) bad.push(`${name}: width ${r.width}`);
          if (!(r.width >= Math.min(320, shown - 32)))
            bad.push(`${name}: width ${r.width} for an obstacle ${shown} px`);
          if (!(r.edge <= vw - 16 && r.edge - r.width >= 0))
            bad.push(`${name}: outside the window`);
        });
      });
    });
    expect({ count: bad.length, first: bad.slice(0, 10) }).toEqual({
      count: 0,
      first: [],
    });
  });
});

describe("toast placement beside a drawer or a custom panel is unchanged", () => {
  it("moves left of the Create Label drawer at 1920×1080, where 360 px or more is left", () => {
    // 60vw = 1152 px wide, from x=768.
    const r = measureAt(1920, 1080, [createLabelDrawer(1920, 1080)]);
    expect(r).toEqual({
      room: 768 - 16 - 216,
      hidden: false,
      right: 1920 - (768 - 16),
      edge: 768 - 16,
    });
  });

  it("leaves the column where it is for a custom panel that leaves no room", () => {
    const panel = holder("[data-fa-overlay-panel]", rect(240, 56, 1440, 900));
    expect(measureAt(1440, 900, [panel])).toEqual({
      room: Infinity,
      hidden: false,
      right: 16,
      edge: 1424,
    });
  });

  it("moves left of a custom panel that leaves room", () => {
    const panel = holder("[data-fa-overlay-panel]", rect(900, 56, 1440, 900));
    expect(measureAt(1440, 900, [panel])).toEqual({
      room: 900 - 16 - 216,
      hidden: false,
      right: 1440 - (900 - 16),
      edge: 900 - 16,
    });
  });
});
