import { useLayoutEffect, useState } from "react";

// The catalog's filter panel is a sticky column beside the grid. It scrolls
// by itself and never runs past the bottom of the window, wherever the page
// header and the cards above it end: its height is measured from where it
// sits on screen. It is measured again when the window resizes, when
// anything scrolls, when the layout around it changes size (an alert
// appearing above it) and each time the panel mounts (a window widened from
// the narrow layout, where the filters are a drawer).
export const STICKY_TOP_PX = 12;
const BOTTOM_GAP_PX = 16;
const MIN_HEIGHT_PX = 240;

export function fitHeight(top, viewportHeight) {
  const room = Math.floor(viewportHeight - Math.max(top, 0) - BOTTOM_GAP_PX);
  return Math.max(MIN_HEIGHT_PX, room);
}

export function useStickyFit() {
  // A callback ref, so a panel that mounts later (or again) is measured.
  const [el, setEl] = useState(null);
  const [maxHeight, setMaxHeight] = useState(undefined);
  useLayoutEffect(() => {
    if (!el || typeof window === "undefined") return undefined;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const next = fitHeight(
        el.getBoundingClientRect().top,
        window.innerHeight,
      );
      setMaxHeight((prev) => (prev === next ? prev : next));
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("resize", schedule);
    // Capture: the page may scroll inside a layout box, not the window.
    window.addEventListener("scroll", schedule, true);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(schedule);
    if (observer) {
      observer.observe(document.body);
      if (el.parentElement) observer.observe(el.parentElement);
    }
    return () => {
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", schedule, true);
      if (observer) observer.disconnect();
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [el]);
  return [setEl, maxHeight];
}
