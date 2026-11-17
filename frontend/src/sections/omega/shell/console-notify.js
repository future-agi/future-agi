// Opt-in browser notifications for the Console's Inbox (CP P15).
//
// Nothing is shown until the user turns them on from the product switcher,
// which asks the browser for permission from that click. Then one
// notification goes out when the Inbox count rises while this tab is hidden.
// The choice is a per-browser preference in localStorage; it holds no data.
import { useSyncExternalStore } from "react";

import { inboxLabel } from "./console-inbox";

export const NOTIFY_STATE = Object.freeze({
  UNSUPPORTED: "unsupported",
  OFF: "off",
  ON: "on",
  BLOCKED: "blocked",
});

export const NOTIFY_PREFERENCE_KEY = "omega.console.inboxNotifications";
export const NOTIFICATION_TAG = "omega-console-inbox";
const listeners = new Set();
let version = 0;
const changed = () => {
  version += 1;
  listeners.forEach((listener) => listener());
};

const api = () =>
  typeof window !== "undefined" && typeof window.Notification === "function"
    ? window.Notification
    : null;

function readPreference() {
  try {
    return window.localStorage.getItem(NOTIFY_PREFERENCE_KEY) === "on";
  } catch {
    return false;
  }
}

function writePreference(on) {
  try {
    if (on) window.localStorage.setItem(NOTIFY_PREFERENCE_KEY, "on");
    else window.localStorage.removeItem(NOTIFY_PREFERENCE_KEY);
  } catch {
    // Storage blocked: the choice lasts for this page only.
  }
}

export function notificationState() {
  const Notification = api();
  if (!Notification) return NOTIFY_STATE.UNSUPPORTED;
  if (Notification.permission === "denied") return NOTIFY_STATE.BLOCKED;
  return Notification.permission === "granted" && readPreference()
    ? NOTIFY_STATE.ON
    : NOTIFY_STATE.OFF;
}

/** Call from a click: asks for permission, then turns them on if granted. */
export async function enableInboxNotifications() {
  const Notification = api();
  if (!Notification) return NOTIFY_STATE.UNSUPPORTED;
  let permission = Notification.permission;
  if (permission === "default") {
    try {
      permission = await Notification.requestPermission();
    } catch {
      permission = Notification.permission;
    }
  }
  if (permission === "granted") writePreference(true);
  changed();
  return notificationState();
}

export function disableInboxNotifications() {
  writePreference(false);
  changed();
}

/**
 * One notification when the count rose and this tab is hidden.
 * @returns {boolean} whether one was shown
 */
export function maybeNotifyInbox(previous, next, doc = document) {
  if (notificationState() !== NOTIFY_STATE.ON) return false;
  if (doc?.visibilityState !== "hidden") return false;
  if (!Number.isInteger(next) || next <= (previous ?? 0)) return false;
  try {
    const Notification = api();
    const shown = new Notification("Omega Console", {
      body: `${inboxLabel(next)} in Omega Console`,
      tag: NOTIFICATION_TAG,
    });
    shown.onclick = () => {
      window.focus();
      shown.close();
    };
    return true;
  } catch {
    return false;
  }
}

const subscribe = (listener) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const snapshot = () => version;

export function useNotificationState() {
  useSyncExternalStore(subscribe, snapshot, snapshot);
  return notificationState();
}
