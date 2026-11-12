// Where toasts sit: bottom-right of the page content (`<main>`). A toast
// never covers the left nav, page headers, the bottom-right Falcon bubble,
// the run tray chip, a bottom-centre bar (selection, pending changes,
// verdict) or a grid footer. Next to an open right drawer or dialog it moves
// left of it, or shows over it when there is no room beside it, and it is
// never hidden. Two `<body>` vars drive the container (snackbar-provider
// GlobalStyles):
// - `--fa-toast-right`  the CSS `right` inset (from the window's right edge)
//   that puts the column's right edge 16 px inside the content's right edge,
//   16 px left of an open drawer / dialog over the column, or 16 px inside a
//   drawer / dialog the column shows over;
// - `--fa-toast-lift`  how far to raise the stack so it clears any fixed bar
//   or launcher at the bottom of the screen that overlaps the toast column
//   (measured, so bars from any screen are covered without each one opting in).
// `--fa-toast-offset` (set by VerdictBar / PendingChangesBar) still works:
// the container uses the larger of the two.

export const TOAST_RIGHT_VAR = "--fa-toast-right";
export const TOAST_LIFT_VAR = "--fa-toast-lift";
/** Width the toast column is measured over (the toast max width). */
export const TOAST_COLUMN_PX = 420;
const GAP = 16;
/** How high above the bottom edge a bar may start and still be lifted over. */
const BAND_PX = 180;
/**
 * Small fixed launchers at the bottom right, checked by their box as well as
 * by the hit test (which samples three points and can miss one that only
 * clips the column's edge): the Falcon bubble (a fixed MUI Fab, 40 x 40 at
 * bottom 72 / right 12, FalconAIFab.jsx) and the run tray chip (RunTray.jsx).
 */
const LAUNCHERS = ".MuiFab-root, [data-testid='run-tray']";

const isToast = (el) => Boolean(el?.closest?.(".notistack-SnackbarContainer"));

/** The nearest fixed-position ancestor (or self) that is a bar, else null. */
function fixedBarOf(el, view, doc) {
  let cur = el;
  while (cur && cur !== doc.body && cur !== doc.documentElement) {
    if (isToast(cur)) return null;
    const style = view.getComputedStyle(cur);
    if (style.position === "fixed") {
      const r = cur.getBoundingClientRect();
      // A bar, not a full-height panel or overlay.
      if (r.height > 0 && r.height < BAND_PX && r.width > 0) return r;
      return null;
    }
    cur = cur.parentElement;
  }
  return null;
}

/** The width CSS `right` on a fixed box is measured from (no scrollbar). */
function viewWidth(doc, view) {
  return doc.documentElement?.clientWidth || view.innerWidth || 0;
}

/**
 * How far to lift a toast column whose right edge is at x = `edge` so it
 * clears fixed bars, launchers and marked in-flow footers at the bottom.
 */
function liftAt(edge, doc, view) {
  const h = view.innerHeight;
  const colLeft = edge - TOAST_COLUMN_PX;
  let lift = 0;
  if (typeof doc.elementsFromPoint === "function") {
    const xs = [24, TOAST_COLUMN_PX / 2, TOAST_COLUMN_PX - 24].map(
      (dx) => edge - dx,
    );
    for (let y = h - 12; y > h - BAND_PX; y -= 24)
      xs.forEach((x) => {
        doc.elementsFromPoint(x, y).forEach((el) => {
          const r = fixedBarOf(el, view, doc);
          if (r && r.bottom > h - BAND_PX)
            lift = Math.max(lift, Math.round(h - r.top + 8));
        });
      });
  }
  // An in-flow footer at the bottom of a grid (row counts, the selected
  // count, key hints) is cleared too. A page marks it with
  // `data-toast-clear`; the results grid's footer row is known by its key
  // hints. The Falcon bubble and the run tray chip sit at the bottom right,
  // inside the right-hand column, so they are cleared by box.
  if (typeof doc.querySelectorAll === "function") {
    const footers = [
      ...doc.querySelectorAll("[data-toast-clear]"),
      ...[...doc.querySelectorAll('[data-testid="results-key-hints"]')]
        .map((el) => el.parentElement)
        .filter(Boolean),
      ...[...doc.querySelectorAll(LAUNCHERS)].filter(
        (el) => view.getComputedStyle?.(el)?.position === "fixed",
      ),
    ];
    footers.forEach((el) => {
      const r = el.getBoundingClientRect();
      if (!r.height || r.top < h - BAND_PX || r.top >= h) return;
      if (r.right <= colLeft || r.left >= edge) return;
      lift = Math.max(lift, Math.round(h - r.top + 8));
    });
  }
  return lift;
}

/**
 * Where the toast column sits right now. Pure over the DOM it is given
 * (tests pass a stub).
 * @returns {{right: number, lift: number, edge: number, start: number}}
 *   `right` the CSS inset from the window's right edge; `edge` the x of the
 *   column's right edge (the content's right edge − 16 px); `start` the x
 *   the column never reaches past next to a drawer (the content's left
 *   edge + 16 px); `lift` the px to raise it by.
 */
export function measureToastPlacement(doc = document, view = window) {
  const vw = viewWidth(doc, view);
  const main =
    doc.getElementById("main-content") ?? doc.querySelector("main") ?? null;
  const raw = main?.getBoundingClientRect?.() ?? null;
  // A `<main>` with no size (hidden, not laid out yet) gives no edge: fall
  // back to the window, so the column is never placed off-screen.
  const box =
    raw && !((raw.width ?? 1) === 0 && (raw.right ?? 1) === 0) ? raw : null;
  const mainLeft = box ? Math.max(0, box.left ?? 0) : 0;
  // `<main>` scrolls (overflow: auto): its scrollbar is not content.
  let mainRight = vw;
  if (box && main.clientWidth > 0)
    mainRight = (box.left ?? 0) + (main.clientLeft || 0) + main.clientWidth;
  else if (box) mainRight = box.right ?? vw;
  if (vw > 0) mainRight = Math.min(vw, mainRight);
  const edge = Math.round(mainRight - GAP);
  const start = Math.round(mainLeft + GAP);
  const right = Math.round(vw - edge);
  return { right, lift: liftAt(edge, doc, view), edge, start };
}

// A toast never sits under an open right drawer or a dialog, and is never
// hidden by one. The column moves to the left of it (right edge = its
// left − 16 px) and narrows to the room left there. When that room is under
// MIN_ROOM_PX, the column shows over the drawer or dialog instead: its right
// edge 16 px inside the smaller of the content's and the obstacle's right
// edges, no wider than the obstacle less 16 px each side (and 420 px). When
// that edge leaves the column too narrow to read (under TOAST_MIN_PX, or
// under the obstacle's width less 32 px for an obstacle narrower than that),
// as when a sidebar docked beside the page ends the content well left of a
// right drawer's right edge, the column's right edge goes 16 px inside the
// obstacle's own right edge instead. It is lifted clear of the buttons at
// the obstacle's bottom but never out of the window, and painted above it
// (TOAST_Z_INDEX). A drawer or dialog that does not reach the column (the
// left nav, a dialog left of it or above it) changes nothing, and neither
// does one narrower than MIN_OBSTACLE_PX. A custom right panel
// (`data-fa-overlay-panel`: the experiment result panel, the Iterate dock)
// only moves the column, and only while MIN_ROOM_PX is left beside it (a
// full-width panel leaves the column where it is).
export const TOAST_MAXW_VAR = "--fa-toast-maxw";
export const MIN_ROOM_PX = 360;
/** The narrowest column a toast over a drawer or dialog can be read in. */
const TOAST_MIN_PX = 320;
/** A drawer or dialog narrower than this (in the window) is no obstacle. */
const MIN_OBSTACLE_PX = 64;
/**
 * The toast container's stacking order: above every layer the app gives a
 * drawer, dialog or backdrop (MUI's drawer 1200 and modal 1300, and the 9999
 * several drawers and dialogs set), so a toast shown over one is on top.
 */
export const TOAST_Z_INDEX = 10000;
/** How tall the toast column is taken to be when checking overlaps. */
const COLUMN_HEIGHT_PX = 140;
/** Drawers and dialogs: the column moves left of them, or shows over them. */
const DRAWERS = ".MuiDrawer-paper, .MuiDialog-paper, [role='dialog']";
/** Custom right-hand panels: the column moves left of them while it can. */
const OVERLAY_PANEL = "[data-fa-overlay-panel]";
const OBSTACLES = `${DRAWERS}, ${OVERLAY_PANEL}`;
/** A drawer's or dialog's own controls that a column over it clears. */
const OWN_CONTROLS =
  ".MuiDialogActions-root, [data-toast-clear], button, [role='button']";

/**
 * The column shown over the drawer or dialog `host` (the last one it was
 * moved past): right edge 16 px inside the smaller of the content's and the
 * host's right edges, or inside the host's own right edge when the content's
 * would leave the column too narrow to read; width inside the host, lifted
 * clear of the controls at the bottom of every drawer or dialog under it,
 * never out of the window.
 */
function overObstacle(place, host, boxes, doc, view) {
  const vw = viewWidth(doc, view);
  const h = view.innerHeight;
  const hostRight = vw > 0 ? Math.min(vw, host.right) : host.right;
  const hostLeft = Math.max(0, host.left);
  const readable = Math.min(TOAST_MIN_PX, hostRight - hostLeft - 2 * GAP);
  let edge = Math.round(Math.min(place.edge, hostRight - GAP));
  if (Math.min(TOAST_COLUMN_PX, edge - hostLeft - GAP) < readable)
    edge = Math.round(hostRight - GAP);
  const room = Math.round(edge - hostLeft - GAP);
  const width = Math.max(0, Math.min(TOAST_COLUMN_PX, room));
  const colLeft = edge - width;
  let lift = edge === place.edge ? place.lift : liftAt(edge, doc, view);
  boxes.forEach(({ el, panel }) => {
    if (panel || typeof el.querySelectorAll !== "function") return;
    Array.from(el.querySelectorAll(OWN_CONTROLS)).forEach((c) => {
      const r = c.getBoundingClientRect();
      if (!(r.width > 0 && r.height > 0)) return;
      if (r.top < h - BAND_PX || r.top >= h) return;
      if (r.right <= colLeft || r.left >= edge) return;
      lift = Math.max(lift, Math.round(h - r.top + 8));
    });
  });
  // Never so high that the column leaves the window.
  lift = Math.min(lift, Math.max(0, h - COLUMN_HEIGHT_PX - 2 * GAP));
  return {
    right: place.right + (place.edge - edge),
    edge,
    over: true,
    width,
    lift,
  };
}

/**
 * Where the column goes when an open drawer or dialog overlaps it, and the
 * room it has there (pure over the DOM it is given).
 * @param {{right: number, lift: number, edge: number, start: number}} place
 *   measureToastPlacement()
 * @returns {{room: number, hidden: false, right: number, edge: number,
 *   over?: true, width?: number, lift?: number}}
 *   room in px beside the drawer or dialog (Infinity = free); `right` /
 *   `edge` as in place, moved left of it or, with `over`, onto it, with the
 *   column's `width` and `lift` there. `hidden` is always false: placement
 *   never hides a toast.
 */
export function measureToastRoom(place, doc = document, view = window) {
  const { right = GAP, lift = 0, edge, start = GAP } = place ?? {};
  const free = { room: Infinity, hidden: false, right, edge };
  if (!Number.isFinite(edge)) return free;
  const vw = viewWidth(doc, view);
  const h = view.innerHeight;
  const bottom = h - lift;
  const top = bottom - COLUMN_HEIGHT_PX;
  const els =
    typeof doc.querySelectorAll === "function"
      ? Array.from(doc.querySelectorAll(OBSTACLES))
      : [];
  const boxes = [];
  els.forEach((el) => {
    if (isToast(el)) return;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return;
    // Too narrow to hold a toast: not an obstacle, the column stays where
    // it would be with nothing open.
    const shown =
      (vw > 0 ? Math.min(vw, r.right) : r.right) - Math.max(0, r.left);
    if (shown < MIN_OBSTACLE_PX) return;
    const style = view.getComputedStyle?.(el);
    if (style && (style.visibility === "hidden" || style.display === "none"))
      return;
    // Only what overlaps the toast column's height band matters.
    if (r.bottom <= top || r.top >= bottom) return;
    const panel =
      el.matches?.(OVERLAY_PANEL) === true && el.matches?.(DRAWERS) !== true;
    boxes.push({ el, r, panel });
  });
  // Right to left, so a dialog over a drawer pushes the column on past both.
  boxes.sort((a, b) => b.r.right - a.r.right);
  // Where the column's right edge ends up, and the box that put it there.
  const leftOf = (list) => {
    let x = edge;
    let by = null;
    list.forEach((b) => {
      const { r } = b;
      // Wholly right of the column, or left of where it starts: not over it.
      if (r.left >= x || r.right <= Math.max(start, x - TOAST_COLUMN_PX))
        return;
      x = Math.min(x, Math.round(r.left - GAP));
      by = b;
    });
    return { x, by };
  };
  const past = leftOf(boxes);
  // A custom panel moves the column only while MIN_ROOM_PX is left there;
  // otherwise the column stays as drawers and dialogs alone place it.
  const { x: at, by } =
    past.x === edge || Math.round(past.x - start) >= MIN_ROOM_PX
      ? past
      : leftOf(boxes.filter((b) => !b.panel));
  if (at === edge) return free;
  const room = Math.round(at - start);
  if (room >= MIN_ROOM_PX)
    return { room, hidden: false, right: right + (edge - at), edge: at };
  // No room beside it: shown over the drawer or dialog, never hidden.
  const over = overObstacle({ right, lift, edge }, by.r, boxes, doc, view);
  return { room, hidden: false, ...over };
}

/** Write the vars on `<body>` (no-op without a DOM). */
export function syncToastPlacement() {
  if (typeof document === "undefined") return;
  try {
    const place = measureToastPlacement();
    const spot = measureToastRoom(place);
    const { room, right, edge } = spot;
    // Over a drawer or dialog: its own lift and width. Moved left of a
    // drawer: clear the bars under the column where it is.
    let lift = place.lift;
    if (spot.over) lift = spot.lift;
    else if (edge !== place.edge) lift = liftAt(edge, document, window);
    const maxw = spot.over ? spot.width : room;
    const body = document.body.style;
    body.setProperty(TOAST_RIGHT_VAR, `${right}px`);
    if (lift) body.setProperty(TOAST_LIFT_VAR, `${lift}px`);
    else body.removeProperty(TOAST_LIFT_VAR);
    if (Number.isFinite(maxw)) body.setProperty(TOAST_MAXW_VAR, `${maxw}px`);
    else body.removeProperty(TOAST_MAXW_VAR);
  } catch {
    // Placement is best effort: the default corner still works.
  }
}
