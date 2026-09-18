/**
 * Builder mode — Auto vs Guided.
 *
 * Auto:    the builder derives silently, filling in reasonable
 *          defaults wherever a choice comes up.
 * Guided:  the builder pauses at each real decision and surfaces
 *          it as an AskUserQuestion card in the chat (Claude's own
 *          questionnaire shape).
 *
 * Module-level so the composer selector, the derivation stream and
 * the workspace chat can all read the same state without prop-
 * threading through half the tree. Mirrors builderPromptBus and
 * scenarioSelectionBus.
 */

const MODES = ["auto", "guided"];
const DEFAULT_MODE = "auto";

const listeners = new Set();
let current = DEFAULT_MODE;

export const BUILDER_MODES = [
  {
    id: "auto",
    label: "Auto",
    hint: "Build without asking — I'll fill in reasonable defaults",
    icon: "solar:magic-stick-3-linear",
  },
  {
    id: "guided",
    label: "Manual",
    hint: "Ask me before major decisions",
    icon: "solar:question-circle-linear",
  },
];

export function getBuilderMode() {
  return current;
}

export function subscribeBuilderMode(fn) {
  listeners.add(fn);
  try { fn(current); } catch { /* no-op */ }
  return () => { listeners.delete(fn); };
}

export function setBuilderMode(next) {
  if (!MODES.includes(next)) return;
  if (next === current) return;
  current = next;
  listeners.forEach((fn) => {
    try { fn(current); } catch { /* no-op */ }
  });
}
