import { useEffect, useId, useSyncExternalStore } from "react";

/*
  One side drawer on screen at a time.

  A drawer opened from inside another one (Build environment → Vapi, a
  scenario → its persona, a run → its eval) used to slide in on top of it,
  leaving two panels overlapped. Every right-side drawer registers here while
  it is open; only the newest is shown, the ones behind it are hidden. They
  stay mounted, so closing the new drawer brings back the one it was opened
  from with everything picked there still in place.
*/

let stack = [];
const listeners = new Set();
const emit = () => listeners.forEach((l) => l());
const subscribe = (l) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const topId = () => (stack.length ? stack[stack.length - 1] : null);

/**
 * True while a drawer opened after this one is still open.
 * `overlay: true` is for side panels opened from a drawer to work on what it
 * shows (annotate, label): they stay out of the stack, so the drawer they were
 * opened from stays visible beside them.
 */
export function useDrawerCovered(open, { overlay = false } = {}) {
  const id = useId();
  const registered = Boolean(open) && !overlay;
  useEffect(() => {
    if (!registered) return undefined;
    stack = [...stack.filter((x) => x !== id), id];
    emit();
    return () => {
      stack = stack.filter((x) => x !== id);
      emit();
    };
  }, [registered, id]);
  const top = useSyncExternalStore(subscribe, topId, topId);
  return registered && top !== null && top !== id;
}

/* Merged into the drawer's root `sx` (a Drawer, or a Dialog laid out as a
   side panel): the panel and its backdrop are hidden, and clicks go to the
   drawer on top. */
export const COVERED_DRAWER_SX = {
  pointerEvents: "none",
  "& .MuiDrawer-paper, & .MuiDialog-container, & .MuiBackdrop-root": {
    visibility: "hidden",
  },
  // Children too: some libraries (the trace graph) set their own
  // visibility: visible, which would show through a hidden panel.
  "& .MuiDrawer-paper *, & .MuiDialog-container *": { visibility: "hidden" },
};

export const coveredDrawerSx = (covered) =>
  covered ? COVERED_DRAWER_SX : undefined;
