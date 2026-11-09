// Where toasts sit (IT-R1 G2 P8). Bottom-left of the page content (`<main>`),
// so they never cover the left nav, page headers, right drawers, the
// bottom-right Falcon bubble or a bottom-centre bar (selection, pending
// changes, verdict). Two `<body>` vars drive the container (snackbar-provider
// GlobalStyles):
// - `--fa-toast-left`  the content's left edge + 16 px;
// - `--fa-toast-lift`  how far to raise the stack so it clears any fixed bar
//   at the bottom of the screen that overlaps the toast column (measured, so
//   bars from any screen are covered without each one opting in).
// `--fa-toast-offset` (set by VerdictBar / PendingChangesBar) still works:
// the container uses the larger of the two.

export const TOAST_LEFT_VAR = "--fa-toast-left";
export const TOAST_LIFT_VAR = "--fa-toast-lift";
/** Width the toast column is measured over (the toast max width). */
export const TOAST_COLUMN_PX = 420;
const GAP = 16;
/** How high above the bottom edge a bar may start and still be lifted over. */
const BAND_PX = 180;

const isToast = (el) => Boolean(el?.closest?.(".notistack-SnackbarContainer"));

/** The nearest fixed-position ancestor (or self) that is a bar, else null. */
function fixedBarOf(el, view) {
  let cur = el;
  while (cur && cur !== document.body && cur !== document.documentElement) {
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

/**
 * The left edge and lift for the toast column right now. Pure over the DOM
 * it is given (tests pass a stub).
 * @returns {{left: number, lift: number}}
 */
export function measureToastPlacement(doc = document, view = window) {
  const main =
    doc.getElementById("main-content") ?? doc.querySelector("main") ?? null;
  const mainLeft = main ? Math.max(0, main.getBoundingClientRect().left) : 0;
  const left = Math.round(mainLeft + GAP);
  const h = view.innerHeight;
  let lift = 0;
  if (typeof doc.elementsFromPoint === "function") {
    const xs = [24, TOAST_COLUMN_PX / 2, TOAST_COLUMN_PX - 24].map(
      (dx) => left + dx,
    );
    for (let y = h - 12; y > h - BAND_PX; y -= 24)
      xs.forEach((x) => {
        doc.elementsFromPoint(x, y).forEach((el) => {
          const r = fixedBarOf(el, view);
          if (r && r.bottom > h - BAND_PX)
            lift = Math.max(lift, Math.round(h - r.top + 8));
        });
      });
  }
  // IT-R5 G2 S2: an in-flow footer at the bottom of a grid (row counts,
  // the selected count, key hints) is cleared too. A page marks it with
  // `data-toast-clear`; the results grid's footer row is known by its key
  // hints.
  if (typeof doc.querySelectorAll === "function") {
    const right = left + TOAST_COLUMN_PX;
    const footers = [
      ...doc.querySelectorAll("[data-toast-clear]"),
      ...[...doc.querySelectorAll('[data-testid="results-key-hints"]')]
        .map((el) => el.parentElement)
        .filter(Boolean),
    ];
    footers.forEach((el) => {
      const r = el.getBoundingClientRect();
      if (!r.height || r.top < h - BAND_PX || r.top >= h) return;
      if (r.right <= left || r.left >= right) return;
      lift = Math.max(lift, Math.round(h - r.top + 8));
    });
  }
  return { left, lift };
}

// IT-R2 G2 P10 (verified: `Added #4A · the coding harness Code` landed ON the New
// experiment drawer at 1280×720): a toast never intersects an open right
// drawer or a dialog. The column narrows to the room left of it; below
// MIN_ROOM_PX there is no toast at all — the drawer's own status line says it.
export const TOAST_MAXW_VAR = "--fa-toast-maxw";
export const TOAST_HIDDEN_CLASS = "fa-toasts-hidden";
export const MIN_ROOM_PX = 360;
/** How tall the toast column is taken to be when checking overlaps. */
const COLUMN_HEIGHT_PX = 140;
const OBSTACLES = ".MuiDrawer-paper, .MuiDialog-paper, [role='dialog']";

/**
 * The room the toast column has between its left edge and the first open
 * drawer or dialog that overlaps it (pure over the DOM it is given).
 * @param {{left: number, lift: number}} place  measureToastPlacement()
 * @returns {{room: number, hidden: boolean}}  room in px (Infinity = free)
 */
export function measureToastRoom(place, doc = document, view = window) {
  const { left, lift = 0 } = place ?? {};
  const h = view.innerHeight;
  const bottom = h - lift;
  const top = bottom - COLUMN_HEIGHT_PX;
  let limit = Infinity;
  const els =
    typeof doc.querySelectorAll === "function"
      ? Array.from(doc.querySelectorAll(OBSTACLES))
      : [];
  els.forEach((el) => {
    if (isToast(el)) return;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return;
    const style = view.getComputedStyle?.(el);
    if (style && (style.visibility === "hidden" || style.display === "none"))
      return;
    // Only what overlaps the toast column's height band matters.
    if (r.bottom <= top || r.top >= bottom) return;
    if (r.right <= left) return;
    limit = Math.min(limit, r.left - GAP);
  });
  const room = limit === Infinity ? Infinity : Math.round(limit - left);
  return { room, hidden: room < MIN_ROOM_PX };
}

/** Write the vars on `<body>` (no-op without a DOM). */
export function syncToastPlacement() {
  if (typeof document === "undefined") return;
  try {
    const place = measureToastPlacement();
    const { left, lift } = place;
    const body = document.body.style;
    body.setProperty(TOAST_LEFT_VAR, `${left}px`);
    if (lift) body.setProperty(TOAST_LIFT_VAR, `${lift}px`);
    else body.removeProperty(TOAST_LIFT_VAR);
    const { room, hidden } = measureToastRoom(place);
    if (Number.isFinite(room) && !hidden)
      body.setProperty(TOAST_MAXW_VAR, `${room}px`);
    else body.removeProperty(TOAST_MAXW_VAR);
    document.body.classList.toggle(TOAST_HIDDEN_CLASS, hidden);
  } catch {
    // Placement is best effort: the default corner still works.
  }
}
