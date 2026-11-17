// Where a picker popover fits inside the drawer it was opened from: below
// the anchor when its content fits there, else above when it fits there,
// else the roomier side; its height is capped to that side's room so it
// never runs over the drawer footer or header (RT-1 / critic r3 #6).
// Pure; RunTargetPicker measures the rects and applies the result.

export const POPOVER_GAP = 8;
export const POPOVER_MAX = 360;
export const POPOVER_MIN = 140;

/**
 * @param {{top: number, bottom: number}} anchor  anchor rect (viewport px)
 * @param {{top: number, bottom: number}} bounds  visible area the popover
 *   must stay inside (the drawer's scroll body, between header and footer)
 * @param {number} [needed]  estimated content height (px); defaults to max
 * @param {{max?: number, min?: number, gap?: number}} [opts]
 * @returns {{place: "below" | "above", maxHeight: number}}
 */
export function popoverPlacement(anchor, bounds, needed, opts = {}) {
  const max = opts.max ?? POPOVER_MAX;
  const min = opts.min ?? POPOVER_MIN;
  const gap = opts.gap ?? POPOVER_GAP;
  const want = Math.min(max, Math.max(min, needed ?? max));
  const below = Math.floor(bounds.bottom - anchor.bottom - gap);
  const above = Math.floor(anchor.top - bounds.top - gap);
  let place;
  if (below >= want) place = "below";
  else if (above >= want) place = "above";
  else place = above > below ? "above" : "below";
  const room = place === "below" ? below : above;
  return { place, maxHeight: Math.max(min, Math.min(want, room)) };
}

/** The nearest scrolling ancestor's rect, else the viewport. */
export function scrollBoundsOf(el) {
  const viewport = {
    top: 0,
    bottom: typeof window === "undefined" ? 800 : window.innerHeight,
  };
  if (!el || typeof window === "undefined") return viewport;
  for (
    let a = el.parentElement;
    a && a !== document.body;
    a = a.parentElement
  ) {
    const oy = window.getComputedStyle(a).overflowY;
    if ((oy === "auto" || oy === "scroll") && a.clientHeight > 0) {
      const r = a.getBoundingClientRect();
      return {
        top: Math.max(viewport.top, r.top),
        bottom: Math.min(viewport.bottom, r.bottom),
      };
    }
  }
  return viewport;
}
