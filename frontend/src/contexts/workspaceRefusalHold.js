// One workspace_unavailable refusal could reach the person twice: as the
// recovery's own line (which says where they landed) and as a red toast of the
// server's text from the screen that made the call. While a recovery runs, and
// for a short grace after it lands, refusals are held here:
// - the axios interceptor claims each refusal and marks the rejected error
//   handled, so the shared error handlers skip it, and
// - the snackbar gate (components/snackbar/notistack-gate.js), which every
//   notistack import resolves to, drops any toast of the held text.
// The recovery releases the hold before it shows the refusal itself (the
// landing read failed or timed out). When it ends with no line of its own
// (canApply() false: torn down, or a workspace was set during the read), what
// was dropped while it ran is shown after all, so a click never fails silently.

export const WORKSPACE_RECOVERY_HANDLED = "workspace-recovery";

// Shorter texts are matched exactly, never as part of a longer message.
const MIN_CONTAINED_TEXT = 24;
const MESSAGE_FIELDS = ["message", "detail", "error", "result"];

let depth = 0;
let graceTimer = null;
const heldTexts = new Set();
// Replays of what was dropped while a recovery ran, for an unexplained end.
let kept = [];
let lastEndUnexplained = false;

const holding = () => depth > 0 || graceTimer !== null;

function clearHold() {
  clearTimeout(graceTimer);
  graceTimer = null;
  heldTexts.clear();
  kept = [];
}

// Starts a hold; returns its release. release(graceMs) keeps the held texts
// for graceMs more (screens may toast a little after the landing); release()
// ends the hold at once. release(0, { unexplained: true }) ends it and then
// replays what it dropped, because no line explained the refusal. Releasing
// twice is a no-op.
export function holdWorkspaceRefusals() {
  clearTimeout(graceTimer);
  graceTimer = null;
  depth += 1;
  let released = false;
  return (graceMs = 0, { unexplained = false } = {}) => {
    if (released) return;
    released = true;
    depth -= 1;
    // An explained end (a landing line or the hook's own error) covers them.
    if (!unexplained) kept = [];
    if (depth > 0) return;
    lastEndUnexplained = unexplained;
    if (graceMs > 0 && !unexplained) {
      clearTimeout(graceTimer);
      graceTimer = setTimeout(clearHold, graceMs);
      return;
    }
    const replays = kept;
    clearHold();
    for (const replay of replays) {
      try {
        replay();
      } catch {
        // One screen's failing toast must not hide the others.
      }
    }
  };
}

// For a toast or handler skipped because of a handled refusal. While the
// recovery runs, the replay is kept until it ends (dropped when it explains
// the refusal). After the hold, it runs now only if the last recovery ended
// unexplained; after a landing line it stays dropped.
export function keepForUnexplainedRecovery(replay) {
  if (depth > 0) kept.push(replay);
  else if (graceTimer === null && lastEndUnexplained) replay();
}

// Called by the interceptor for each refusal. True when a recovery explains
// it; its exact server texts are then held.
export function claimWorkspaceRefusal(data) {
  if (!holding()) return false;
  for (const field of MESSAGE_FIELDS) {
    const text = data?.[field];
    if (typeof text === "string" && text.trim()) heldTexts.add(text.trim());
  }
  return true;
}

export function isHeldRefusalToast(message) {
  if (!holding() || typeof message !== "string") return false;
  const text = message.trim();
  for (const held of heldTexts) {
    if (
      text === held ||
      (held.length >= MIN_CONTAINED_TEXT && text.includes(held))
    )
      return true;
  }
  return false;
}

export const isWorkspaceRecoveryHandled = (error) =>
  error?.handledBy === WORKSPACE_RECOVERY_HANDLED;
