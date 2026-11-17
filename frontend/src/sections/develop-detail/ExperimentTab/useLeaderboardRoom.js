// The leaderboard grid's unfrozen room in px (P5: default columns fit the
// window). One ResizeObserver on `<main>` for every caller, so the toolbar
// (`N hidden`) and the grid compute the same default set. Bucketed to 8 px
// so a drag-resize doesn't re-shape the grid on every pixel.
import { useSyncExternalStore } from "react";
import { gridRoomFor } from "./leaderboardColumns";

const listeners = new Set();
let observer = null;
let current = null;

const measure = () => {
  if (typeof window === "undefined") return null;
  const main = document.querySelector("main");
  const mainWidth = main?.clientWidth ?? window.innerWidth - 200;
  const room = gridRoomFor(mainWidth, window.innerWidth);
  return Math.floor(room / 8) * 8;
};

const update = () => {
  const next = measure();
  if (next === current) return;
  current = next;
  listeners.forEach((l) => l());
};

function subscribe(listener) {
  listeners.add(listener);
  if (listeners.size === 1 && typeof window !== "undefined") {
    window.addEventListener("resize", update);
    const main = document.querySelector("main");
    if (main && typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(update);
      observer.observe(main);
    }
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size && typeof window !== "undefined") {
      window.removeEventListener("resize", update);
      observer?.disconnect();
      observer = null;
    }
  };
}

const snapshot = () => {
  if (current == null) current = measure();
  return current;
};

/** @returns {number|null} px of the grid's scrolling part */
export default function useLeaderboardRoom() {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}
