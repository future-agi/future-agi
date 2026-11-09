// The Console's Inbox count, for Future AGI's own nav (CP P15).
//
// The framed Console reports it with embed.badge whenever it changes (its
// count follows the event stream). This store holds the last report, in
// memory only, and the product switcher shows it in both navs. It is honest
// about its limits: the count moves only while the Console page is open,
// because only the frame reports it. Back on a Future AGI page the switcher
// shows the last report from this tab, and drops it after STALE_AFTER_MS
// without a new one rather than show a number nobody is keeping current.
import { useSyncExternalStore } from "react";

export const STALE_AFTER_MS = 10 * 60 * 1000;

let current = null; // { inbox, at }
let staleTimer = null;
const listeners = new Set();
const notify = () => listeners.forEach((listener) => listener());

function clearStaleTimer() {
  if (staleTimer) clearTimeout(staleTimer);
  staleTimer = null;
}

/** A count from the Console: 0-9999 (the bridge has checked it). */
export function reportConsoleInbox(inbox, now = Date.now()) {
  if (!Number.isInteger(inbox) || inbox < 0) return;
  const previous = current?.inbox ?? null;
  current = { inbox, at: now };
  clearStaleTimer();
  staleTimer = setTimeout(() => {
    current = null;
    staleTimer = null;
    notify();
  }, STALE_AFTER_MS);
  if (previous !== inbox) notify();
}

export function resetConsoleInboxForTests() {
  clearStaleTimer();
  current = null;
  notify();
}

const subscribe = (listener) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const snapshot = () => current?.inbox ?? null;

/** The last count (null before the first report and once it went stale). */
export function useConsoleInbox() {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function readConsoleInbox() {
  return snapshot();
}

/** "3 approvals waiting" style label, or null for none. */
export function inboxLabel(inbox) {
  if (!Number.isInteger(inbox) || inbox <= 0) return null;
  return inbox === 1 ? "1 approval waiting" : `${inbox} approvals waiting`;
}

/** The count as a badge shows it. */
export const badgeText = (inbox) => (inbox > 99 ? "99+" : String(inbox));
