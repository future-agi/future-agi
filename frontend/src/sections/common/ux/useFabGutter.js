// Falcon bubble policy (R3 G0 §B7; DL#41, RV#2, AS#20). The bubble sits
// bottom-right (16 px, 40 px) and never covers content:
// - `--fa-fab-gutter` on <body> is `56px` while it shows, else `0px`; the
//   dashboard <main> pads its bottom by it and every bottom-anchored
//   scroller ends above it (`paddingBottom: "var(--fa-fab-gutter)"`).
// - Hidden while the Falcon sidebar is open, while any right drawer, dialog
//   or the Row panel is open, on side by side (`/compare/tasks`) and the
//   review page (they show a top-bar `Ask Falcon ⌘J` instead), and while a
//   screen calls `useHideFab(true)`.
import { useEffect, useState } from "react";
import { create } from "zustand";

export const FAB_GUTTER_VAR = "--fa-fab-gutter";
export const FAB_GUTTER_PX = 56;
/** sx for a bottom-anchored scroller: `{ pb: FAB_GUTTER_PB }`. */
export const FAB_GUTTER_PB = `var(${FAB_GUTTER_VAR}, 0px)`;

const HIDE_ROUTES = [
  /\/compare\/tasks\/?$/,
  /^\/dashboard\/develop\/[^/]+\/review\//,
  /^\/dashboard\/falcon-ai/,
];

/** True on routes that never show the bubble. */
export const fabHiddenOnRoute = (pathname) =>
  HIDE_ROUTES.some((re) => re.test(pathname ?? ""));

const OVERLAY_SELECTOR = [
  ".MuiDrawer-paperAnchorRight",
  '[role="dialog"]',
  '[data-testid="row-panel"]',
  "[data-fa-hide-fab]",
].join(", ");

const isShown = (el) => {
  if (!el || el.closest(".MuiModal-hidden")) return false;
  const style = window.getComputedStyle(el);
  if (style.visibility === "hidden" || style.display === "none") return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
};

/** True while a right drawer, a dialog or the Row panel is on screen. */
export function overlayOnScreen(
  root = typeof document !== "undefined" ? document : null,
) {
  if (!root) return false;
  return [...root.querySelectorAll(OVERLAY_SELECTOR)].some(isShown);
}

const useHideStore = create((set) => ({
  hiders: 0,
  inc: () => set((s) => ({ hiders: s.hiders + 1 })),
  dec: () => set((s) => ({ hiders: Math.max(0, s.hiders - 1) })),
}));

/** Hide the bubble while `active` (a shell the observer can't see). */
export function useHideFab(active = true) {
  useEffect(() => {
    if (!active) return undefined;
    useHideStore.getState().inc();
    return () => useHideStore.getState().dec();
  }, [active]);
}

/** Watches the DOM for overlays (rAF-throttled MutationObserver). */
export function useOverlayOpen() {
  const [open, setOpen] = useState(false);
  const hiders = useHideStore((s) => s.hiders);
  useEffect(() => {
    if (typeof MutationObserver === "undefined") return undefined;
    let frame = 0;
    const check = () => {
      frame = 0;
      setOpen(overlayOnScreen());
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(check);
    };
    const mo = new MutationObserver(schedule);
    mo.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "style", "aria-hidden"],
    });
    // Transitions finish after the last mutation: re-check shortly after.
    const timer = window.setInterval(schedule, 800);
    schedule();
    return () => {
      mo.disconnect();
      window.clearInterval(timer);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  return open || hiders > 0;
}

/** Writes `--fa-fab-gutter` on <body> for the bubble's visibility. */
export default function useFabGutter(visible) {
  useEffect(() => {
    document.body.style.setProperty(
      FAB_GUTTER_VAR,
      visible ? `${FAB_GUTTER_PX}px` : "0px",
    );
  }, [visible]);
  useEffect(
    () => () => document.body.style.setProperty(FAB_GUTTER_VAR, "0px"),
    [],
  );
}
