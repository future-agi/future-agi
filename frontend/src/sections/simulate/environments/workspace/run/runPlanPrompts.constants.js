export const RUN_PLAN_PROMPTS_COPY = {
  draftFix: "The fixes are in unsaved changes. Save them to run them.",
  draftStale:
    "These unsaved changes started from an older version. Discard them on the page, or ask chat to make them again.",
  switchTo: (piece, label) => `The fixes are in ${piece} ${label}.`,
  switchReady: (eligible, requested) =>
    `${eligible} of ${requested} eligible there.`,
  switch: (label) => `Switch to ${label}`,
  keep: "Keep this version",
};
