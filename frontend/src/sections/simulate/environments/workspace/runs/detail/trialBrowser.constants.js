/* What a trial's browser did, as its entry in run detail says it. The
   server reads the trial's own stored file and says every state; a list
   that could not be read is never shown as empty. */
export const TRIAL_BROWSER_COPY = {
  tab: "Browser",
  loadFailed:
    "This trial's browser could not be loaded. Try again in a moment.",
  latestOnly: "The browser is kept for the latest attempt only.",
  states: {
    not_kept: "The list of screenshots was not kept.",
    unreadable: "The list of screenshots could not be read.",
    no_browser: "No browser was started in this trial.",
  },
  none: "No screenshots were taken.",
  levelKeepsNone: "Screenshots are not kept at this run's artifact level.",
  notKept: "Not kept",
  capped: "The trial's screenshots reached their limit; the last one is kept.",
  more: (n) => `And ${n} more screenshot${n === 1 ? "" : "s"}.`,
  incomplete:
    "The browser's record was not finished, so its last moments may be missing.",
  when: { load: "Page loaded", end: "Trial's end" },
  tabLabel: (n) => `Tab ${n}`,
  alt: (url) => (url ? `Screenshot of ${url}` : "Screenshot"),
  blockedHeading: "Refused in the browser",
  blockedUnreadable: "What the browser was refused could not be read.",
  blockedTruncated: "Only the first refusals were kept.",
};

/* An address is a link only when the server allows it and it is http or
   https; anything else is shown as text. */
export const linkable = (one) =>
  one?.link === true && /^https?:\/\//i.test(one?.url || "");

/* The sentences above the pictures, in order. */
export const browserNotes = (data) => {
  if (!data) return [];
  if (data.state !== "listed") {
    const said = TRIAL_BROWSER_COPY.states[data.state];
    return [said || TRIAL_BROWSER_COPY.states.unreadable];
  }
  const notes = [];
  if (data.complete === false) notes.push(TRIAL_BROWSER_COPY.incomplete);
  if (!data.screenshots?.length && !data.more)
    notes.push(TRIAL_BROWSER_COPY.none);
  else if (data.level_keeps_none) notes.push(TRIAL_BROWSER_COPY.levelKeepsNone);
  if (data.capped) notes.push(TRIAL_BROWSER_COPY.capped);
  return notes;
};

/* What a screenshot's caption says before its page. */
export const shotCaption = (one) =>
  [
    TRIAL_BROWSER_COPY.when[one.when] || one.when,
    Number.isInteger(one.tab) ? TRIAL_BROWSER_COPY.tabLabel(one.tab) : null,
  ]
    .filter(Boolean)
    .join(" · ");
