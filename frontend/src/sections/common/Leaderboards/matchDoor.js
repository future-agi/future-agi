import { useSyncExternalStore } from "react";

// HO results R9 (W5 B-H5, door half): the one door from a leaderboard row's
// `Compare with…` to W5 B7's match-by-key dialog. The row's menu lives in
// a grid cell that a live leaderboard re-creates on every refresh, so the
// dialog is held by a host mounted once with the tab (MatchDoorHost); the
// menu item shows only while a host is mounted (the door goes somewhere).

const CLOSED = Object.freeze({ open: false, runs: [], defaultRunId: null });
let state = CLOSED;
let hosts = 0;
const listeners = new Set();

const emit = () => listeners.forEach((l) => l());

/** Open the dialog with `runs` (the dataset's runs) and run A preselected. */
export function openMatchDoor({ runs, defaultRunId } = {}) {
  state = {
    open: true,
    runs: Array.isArray(runs) ? runs : [],
    defaultRunId: defaultRunId ?? null,
  };
  emit();
}

export function closeMatchDoor() {
  if (!state.open) return;
  state = CLOSED;
  emit();
}

/** Called by the host on mount; returns its unmount. */
export function registerMatchHost() {
  hosts += 1;
  emit();
  return () => {
    hosts = Math.max(0, hosts - 1);
    if (!hosts) state = CLOSED;
    emit();
  };
}

const subscribe = (l) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const snapshot = () => state;
const readyNow = () => hosts > 0;

/** `{open, runs, defaultRunId}` for the host. */
export const useMatchDoor = () =>
  useSyncExternalStore(subscribe, snapshot, snapshot);

/** True while a host is mounted (the menu item shows only then). */
export const useMatchDoorReady = () =>
  useSyncExternalStore(subscribe, readyNow, readyNow);
