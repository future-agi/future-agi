// Right overlay panels (iterate r1 G3, rule 7 "Drawers overlay"): the row
// drawer and the Test a fix dock sit fixed on the right, below the page
// header (the page's tab row), and the page behind keeps its widths.
import { useLayoutEffect, useState } from "react";

/**
 * The bottom of the page header above `el`: the topmost visible tab row
 * (`[role=tablist]`) that ends above `el`'s top, else `el`'s top, else
 * `fallback`. Pure over the DOM; exported for tests.
 * @param {Element|null} el
 * @param {{fallback?: number}} [opts]
 */
export function headerBottomAbove(el, { fallback = 0 } = {}) {
  if (typeof document === "undefined") return fallback;
  const elTop = el?.getBoundingClientRect?.().top;
  const limit = Number.isFinite(elTop) ? elTop + 1 : Infinity;
  const bottoms = [...document.querySelectorAll('[role="tablist"]')]
    .filter((t) => !t.closest?.("[data-fa-overlay-panel]"))
    .map((t) => t.getBoundingClientRect())
    .filter((r) => r.height > 0 && r.width > 0 && r.bottom <= limit)
    .map((r) => r.bottom);
  if (bottoms.length) return Math.max(0, Math.round(Math.min(...bottoms)));
  return Number.isFinite(elTop) ? Math.max(0, Math.round(elTop)) : fallback;
}

/** `headerBottomAbove(ref.current)`, re-measured on resize while enabled. */
export function useOverlayTop(ref, enabled = true, { fallback = 0 } = {}) {
  const [top, setTop] = useState(fallback);
  useLayoutEffect(() => {
    if (!enabled || typeof window === "undefined") return undefined;
    const measure = () => setTop(headerBottomAbove(ref.current, { fallback }));
    measure();
    // Headers settle after data loads (verdict lines, chips).
    const t = setTimeout(measure, 300);
    window.addEventListener("resize", measure);
    return () => {
      clearTimeout(t);
      window.removeEventListener("resize", measure);
    };
  }, [ref, enabled, fallback]);
  return top;
}

/** iterate r1 G3 (P9): the row panel's default width, `min(560px, 45vw)`. */
export const overlayDefaultWidth = (windowW) =>
  Math.round(Math.min(560, windowW * 0.45));

/** Clamp a dragged overlay width to [min, window − 320 px]. Pure. */
export const clampOverlayWidth = (w, windowW, min = 400) =>
  Math.round(Math.max(min, Math.min(w, Math.max(min, windowW - 320))));

/**
 * Pointer-drag on the panel's left edge: `onWidth(px)` while moving,
 * `onDone(px)` on release (remember it there).
 */
export function startEdgeDrag(e, { width, onWidth, onDone, min = 400 }) {
  e.preventDefault();
  const x0 = e.clientX;
  let last = width;
  const move = (ev) => {
    last = clampOverlayWidth(width + (x0 - ev.clientX), window.innerWidth, min);
    onWidth(last);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    onDone(last);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}
