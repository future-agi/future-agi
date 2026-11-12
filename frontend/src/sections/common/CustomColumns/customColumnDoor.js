import { useSyncExternalStore } from "react";

// HO results R1 (A-H4): the one door to the results `Add a column` dialog.
// Column ⋮ menus are built as plain item lists (groupMenu.js), so a click
// asks the page's host (CustomColumnHost, mounted with the results toolbar)
// to open the dialog. `initial` = a column to edit, or null for a new one.

let state = { open: false, initial: null };
const listeners = new Set();
let hosts = 0;

const emit = () => listeners.forEach((l) => l());

/** Open the dialog (a new column, or `initial` to edit one). */
export function openCustomColumnDoor(initial = null) {
  state = { open: true, initial: initial ?? null };
  emit();
}

export function closeCustomColumnDoor() {
  if (!state.open) return;
  state = { open: false, initial: null };
  emit();
}

/** True while a results page has a host mounted (the door goes somewhere). */
export const customColumnDoorReady = () => hosts > 0;

/** Called by the host on mount; returns its unmount. */
export function registerCustomColumnHost() {
  hosts += 1;
  return () => {
    hosts = Math.max(0, hosts - 1);
    closeCustomColumnDoor();
  };
}

const subscribe = (l) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const snapshot = () => state;

/** `{open, initial}` for the host. */
export const useCustomColumnDoor = () =>
  useSyncExternalStore(subscribe, snapshot, snapshot);
