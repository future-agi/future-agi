import * as Sentry from "@sentry/react";

// A timing that has not finished within this long was never answered by a
// matching end (for example a filter that left the request unchanged), so
// reporting it would record the wait for the next unrelated refresh.
const MAX_ACTION_MS = 30000;

const pending = new Map();

// Times a user action from the click until its result is on screen. Only the
// start time is remembered; the Sentry transaction (op "ui.action") is created
// when the action ends, so an action that never finishes sends nothing.
// Starting a name that is already pending replaces it.
export const startActionTimer = (name, attributes) => {
  pending.set(name, { startedAt: Date.now(), attributes });
};

export const endActionTimer = (name, outcome = "ok") => {
  const entry = pending.get(name);
  if (!entry) return;
  pending.delete(name);
  if (Date.now() - entry.startedAt > MAX_ACTION_MS) return;
  const span = Sentry.startInactiveSpan({
    name,
    op: "ui.action",
    forceTransaction: true,
    startTime: entry.startedAt / 1000,
    attributes: { ...entry.attributes, outcome },
  });
  span.end();
};
