// One keymap (PRODUCT-SPEC §4.1, C27; R3 G0 §B5). A key means the same
// thing on every surface where it exists. Lanes never edit this file and
// never add raw listeners: they bind handlers by action name with
// `useShortcuts(surface, {action: fn})` (sections/common/ux/useShortcuts.js).
//
// Surfaces: `global` · `list` (dataset list) · `grid` (Data tab) · `row`
// (Row view) · `results` / `drawer` (result grid + drawer) · `matrix` ·
// `taskCompare` (side by side) · `leaderboard` · `newExperiment` ·
// `summary` · `review`, plus `experiments` for `/` search (R1).
//
// Key strings: KeyboardEvent.key values; `mod+X` = Cmd/Ctrl+X;
// `shift+x` = Shift + a letter (it wins over the plain `X` binding on a
// surface that has it; elsewhere Shift+J still means `J`); `alt+X`.
//
// Data only: ShortcutSheet renders it, KeyHintTooltip calls keyLabel(),
// useShortcuts / useRowKeyboardNav resolve events with actionForEvent().

import { isShortcutShown } from "src/launch/launchSwitch";

// L6 launch switch (sheet:shortcuts): a launch build lists and fires only
// the shortcuts of shown surfaces (no Version history, Save a version,
// splits, results, matrix, leaderboard…); prototype builds keep them all.
const liveOn = (e, surface) =>
  surface
    ? Boolean(e.surfaces[surface]) && isShortcutShown(surface, e.action)
    : Object.keys(e.surfaces).some((s) => isShortcutShown(s, e.action));

const IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");

/** Every surface, in the order the sheet lists them. */
export const SURFACES = Object.freeze([
  "global",
  "list",
  "grid",
  "row",
  "results",
  "drawer",
  "matrix",
  "taskCompare",
  "leaderboard",
  "newExperiment",
  "summary",
  "review",
  "experiments",
  "paths",
]);

/** Sheet section titles. */
export const SURFACE_TITLES = Object.freeze({
  global: "Global",
  list: "Dataset list",
  grid: "Data grid",
  row: "Row view",
  results: "Results",
  drawer: "Result drawer",
  matrix: "Matrix",
  taskCompare: "Side by side",
  leaderboard: "Experiments",
  newExperiment: "New experiment",
  summary: "Summary",
  review: "Review",
  experiments: "Experiments",
  paths: "Paths",
});

/** Sheet groups, in order. */
export const GROUPS = Object.freeze(["navigate", "view", "act"]);
export const GROUP_TITLES = Object.freeze({
  navigate: "Navigate",
  view: "View",
  act: "Act",
});

/**
 * @typedef {object} KeymapEntry
 * @property {string} action       stable id used by code
 * @property {string[]} keys       key strings (see the header)
 * @property {string[]} display    key caps shown to the user, in order
 * @property {string} description  one line for the shortcut sheet
 * @property {Object<string,string|true>} surfaces  surface -> true or a
 *           surface-specific description
 * @property {"navigate"|"view"|"act"} [group]  sheet group (default act)
 */

/** @type {KeymapEntry[]} */
export const KEYMAP = [
  // ------------------------------------------------------------ global
  {
    action: "palette",
    keys: ["mod+k"],
    display: ["⌘K"],
    description: "Command palette: go to, search, run a command",
    group: "navigate",
    surfaces: { global: true },
  },
  {
    action: "askFalcon",
    keys: ["mod+j"],
    display: ["⌘J"],
    description: "Ask Falcon",
    group: "act",
    surfaces: { global: true },
  },
  // ------------------------------------------------------------ navigate
  // iterate r3 G3 (P4): ↓ / ↑ move wherever J / K do; ← / → no longer mean
  // next / previous (the Matrix keeps its cell moves, the grid its cells).
  {
    action: "next",
    keys: ["j", "J", "ArrowDown"],
    display: ["J", "↓"],
    description: "Next item",
    group: "navigate",
    surfaces: {
      row: "Next row",
      drawer: "Next row",
      review: "Next item",
      list: "Next dataset",
      grid: "Next row",
      results: "Next row",
      taskCompare: "Next task",
      leaderboard: "Next run",
      paths: "Next path",
    },
  },
  {
    action: "prev",
    keys: ["k", "K", "ArrowUp"],
    display: ["K", "↑"],
    description: "Previous item",
    group: "navigate",
    surfaces: {
      row: "Previous row",
      drawer: "Previous row",
      review: "Previous item",
      list: "Previous dataset",
      grid: "Previous row",
      results: "Previous row",
      taskCompare: "Previous task",
      leaderboard: "Previous run",
      paths: "Previous path",
    },
  },
  // iterate r3 G3 (P4): Home / End / Page Up / Page Down on the results
  // grid and the result drawer.
  {
    action: "first",
    keys: ["Home"],
    display: ["Home"],
    description: "First row",
    group: "navigate",
    surfaces: { results: "First row", drawer: "First row" },
  },
  {
    action: "last",
    keys: ["End"],
    display: ["End"],
    description: "Last row",
    group: "navigate",
    surfaces: { results: "Last row", drawer: "Last row" },
  },
  // L7c g1 (sheet:shortcuts): on the results grid PgDn / PgUp are the
  // grid's own page step (the rows in view), so the sheet says "page".
  {
    action: "pageDown",
    keys: ["PageDown"],
    display: ["PgDn"],
    description: "Ten rows down",
    group: "navigate",
    surfaces: { results: "One page down", drawer: "Ten rows down" },
  },
  {
    action: "pageUp",
    keys: ["PageUp"],
    display: ["PgUp"],
    description: "Ten rows up",
    group: "navigate",
    surfaces: { results: "One page up", drawer: "Ten rows up" },
  },
  {
    action: "nextRegressed",
    keys: ["shift+j"],
    display: ["⇧J"],
    description: "Next regressed row",
    group: "navigate",
    surfaces: { results: true, drawer: true },
  },
  {
    action: "prevRegressed",
    keys: ["shift+k"],
    display: ["⇧K"],
    description: "Previous regressed row",
    group: "navigate",
    surfaces: { results: true, drawer: true },
  },
  {
    action: "nextUnreviewed",
    keys: ["shift+j"],
    display: ["⇧J"],
    description: "Next unreviewed task",
    group: "navigate",
    surfaces: { taskCompare: true },
  },
  {
    action: "prevUnreviewed",
    keys: ["shift+k"],
    display: ["⇧K"],
    description: "Previous unreviewed task",
    group: "navigate",
    surfaces: { taskCompare: true },
  },
  {
    action: "open",
    keys: ["Enter"],
    display: ["↵"],
    description: "Open",
    group: "navigate",
    surfaces: {
      list: "Open dataset",
      grid: "Open Row view",
      results: "Open the result drawer",
      matrix: "Open side by side for this cell",
      leaderboard: "Open run",
      paths: "Open its example tasks",
    },
  },
  {
    action: "tab",
    keys: ["1", "2", "3", "4"],
    display: ["1–4"],
    description: "Switch tab",
    group: "navigate",
    surfaces: { row: "Switch tab (1–4)" },
  },
  {
    action: "goToRow",
    keys: ["mod+g"],
    display: ["⌘G"],
    description: "Go to row",
    group: "navigate",
    surfaces: { grid: true },
  },
  {
    action: "cellLeft",
    keys: ["ArrowLeft"],
    display: ["←"],
    description: "Move to the cell on the left",
    group: "navigate",
    surfaces: { matrix: true },
  },
  {
    action: "cellRight",
    keys: ["ArrowRight"],
    display: ["→"],
    description: "Move to the cell on the right",
    group: "navigate",
    surfaces: { matrix: true },
  },
  {
    action: "cellUp",
    keys: ["ArrowUp"],
    display: ["↑"],
    description: "Move to the cell above",
    group: "navigate",
    surfaces: { matrix: true },
  },
  {
    action: "cellDown",
    keys: ["ArrowDown"],
    display: ["↓"],
    description: "Move to the cell below",
    group: "navigate",
    surfaces: { matrix: true },
  },
  {
    action: "trajectoryTab",
    keys: ["t", "T"],
    display: ["T"],
    description: "Trajectory tab",
    group: "navigate",
    surfaces: { drawer: true, results: true },
  },
  {
    // IT-R4 G3 (P6): from the grid, R opens the row's Review tab with the
    // vote buttons focused (in the open panel R stays Formatted / raw).
    action: "reviewTab",
    keys: ["r", "R"],
    display: ["R"],
    description: "Review this row",
    group: "act",
    surfaces: { results: true },
  },
  {
    action: "openSideBySide",
    keys: ["o", "O"],
    display: ["O"],
    description: "Open side by side",
    group: "navigate",
    surfaces: { drawer: true, results: true },
  },
  {
    action: "nextDiff",
    keys: ["n", "N"],
    display: ["N"],
    description: "Next difference",
    group: "navigate",
    surfaces: { taskCompare: true },
  },
  {
    action: "prevDiff",
    keys: ["p", "P"],
    display: ["P"],
    description: "Previous difference",
    group: "navigate",
    surfaces: { taskCompare: true },
  },
  {
    action: "jumpFailedCheck",
    keys: ["x", "X"],
    display: ["X"],
    description: "Jump to the failed check",
    group: "navigate",
    surfaces: { taskCompare: true },
  },
  {
    action: "wizardNext",
    keys: ["mod+Enter"],
    display: ["⌘↵"],
    description: "Next step, or run",
    group: "navigate",
    surfaces: { newExperiment: true },
  },
  {
    action: "wizardPrevStep",
    keys: ["alt+ArrowLeft"],
    display: ["⌥←"],
    description: "Previous step",
    group: "navigate",
    surfaces: { newExperiment: true },
  },
  {
    action: "wizardNextStep",
    keys: ["alt+ArrowRight"],
    display: ["⌥→"],
    description: "Next step",
    group: "navigate",
    surfaces: { newExperiment: true },
  },
  // Must precede `submit`: on the Row view Cmd+S saves a version (a focused
  // field editor still applies its edit with Cmd+S, see useRowKeyboardNav).
  {
    action: "saveVersion",
    keys: ["mod+s"],
    display: ["⌘S"],
    description: "Save a version",
    group: "act",
    surfaces: { row: true },
  },
  {
    action: "submit",
    keys: ["mod+Enter", "mod+s"],
    display: ["⌘↵"],
    description: "Submit the open form",
    group: "act",
    surfaces: {
      row: "Apply edit",
      drawer: "Submit review",
      review: "Submit and go to the next item",
    },
  },
  {
    action: "skip",
    keys: ["s", "S"],
    display: ["S"],
    description: "Skip this item",
    group: "act",
    surfaces: { drawer: "Skip (Review tab)", review: "Skip this item" },
  },
  {
    action: "splits",
    keys: ["s", "S"],
    display: ["S"],
    description: "Splits",
    group: "act",
    surfaces: { row: "Change splits" },
  },
  {
    action: "pickLeft",
    keys: ["1"],
    display: ["1"],
    description: "Left response is better",
    group: "act",
    surfaces: { drawer: true, review: true },
  },
  {
    action: "pickTie",
    keys: ["2"],
    display: ["2"],
    description: "Tie",
    group: "act",
    surfaces: { drawer: true, review: true },
  },
  {
    action: "pickRight",
    keys: ["3"],
    display: ["3"],
    description: "Right response is better",
    group: "act",
    surfaces: { drawer: true, review: true },
  },
  {
    action: "verdictBaseline",
    keys: ["1"],
    display: ["1"],
    description: "Baseline is better",
    group: "act",
    surfaces: { taskCompare: true },
  },
  {
    action: "verdictTie",
    keys: ["2"],
    display: ["2"],
    description: "Tie",
    group: "act",
    surfaces: { taskCompare: true },
  },
  {
    action: "verdictOther",
    keys: ["3"],
    display: ["3"],
    description: "Focused lane is better",
    group: "act",
    surfaces: { taskCompare: true },
  },
  {
    action: "verdictBothBad",
    keys: ["4"],
    display: ["4"],
    description: "Both are bad",
    group: "act",
    surfaces: { taskCompare: true },
  },
  {
    action: "addRunType",
    keys: ["1", "2", "3", "4"],
    display: ["1–4"],
    description: "Add a run of type 1–4",
    group: "act",
    surfaces: { newExperiment: true },
  },
  {
    action: "prevCandidate",
    keys: [","],
    display: [","],
    description: "Previous candidate run",
    group: "navigate",
    surfaces: { drawer: true, taskCompare: "Focus the previous lane" },
  },
  {
    action: "nextCandidate",
    keys: ["."],
    display: ["."],
    description: "Next candidate run",
    group: "navigate",
    surfaces: { drawer: true, taskCompare: "Focus the next lane" },
  },
  {
    action: "prevTrial",
    keys: ["["],
    display: ["["],
    description: "Previous trial",
    group: "navigate",
    surfaces: { drawer: true, taskCompare: "Previous trial (focused lane)" },
  },
  {
    action: "nextTrial",
    keys: ["]"],
    display: ["]"],
    description: "Next trial",
    group: "navigate",
    surfaces: { drawer: true, taskCompare: "Next trial (focused lane)" },
  },
  // ------------------------------------------------------------ view
  {
    action: "diff",
    keys: ["d", "D"],
    display: ["D"],
    description: "Cycle diff: off, vs expected, vs baseline run",
    group: "view",
    surfaces: { row: "Diff vs expected", drawer: true },
  },
  {
    action: "diffOnly",
    keys: ["d", "D"],
    display: ["D"],
    description: "Differences only",
    group: "view",
    surfaces: { taskCompare: true },
  },
  {
    action: "density",
    keys: ["d", "D"],
    display: ["D"],
    description: "Cycle row density",
    group: "view",
    surfaces: { grid: true, results: true },
  },
  {
    // One view (G-09): the results grid's diff toggle.
    action: "diffToggle",
    keys: ["shift+d"],
    display: ["⇧D"],
    description: "Diff vs baseline or expected",
    group: "view",
    surfaces: { results: true },
  },
  {
    action: "download",
    keys: ["d", "D"],
    display: ["D"],
    description: "Download CSV",
    group: "act",
    surfaces: { summary: true },
  },
  {
    action: "duplicate",
    keys: ["shift+d"],
    display: ["⇧D"],
    description: "Duplicate dataset",
    group: "act",
    surfaces: { list: true },
  },
  {
    action: "raw",
    keys: ["r", "R"],
    display: ["R"],
    description: "Formatted or raw",
    group: "view",
    surfaces: { row: true, drawer: true },
  },
  {
    action: "toggleChart",
    keys: ["r", "R"],
    display: ["R"],
    description: "Table or radar",
    group: "view",
    surfaces: { summary: true },
  },
  {
    // One view (G-09): no Story / Steps / Raw depth modes; M shows or hides
    // the alignment map, I opens the Inspect panel.
    action: "toggleMap",
    keys: ["m", "M"],
    display: ["M"],
    description: "Show or hide the map",
    group: "view",
    surfaces: { taskCompare: true },
  },
  {
    action: "inspect",
    keys: ["i", "I"],
    display: ["I"],
    description: "Inspect panel",
    group: "view",
    surfaces: { taskCompare: true },
  },
  {
    action: "moreMetrics",
    keys: ["m", "M"],
    display: ["M"],
    description: "More metrics",
    group: "view",
    surfaces: { matrix: true },
  },
  // L7c g1 (sheet:shortcuts): not on the Data grid, where Space is the
  // grid's own row select (the X key's twin), so the sheet never says Peek.
  {
    action: "peek",
    keys: [" "],
    display: ["Space"],
    description: "Peek",
    group: "view",
    surfaces: { results: true, drawer: true },
  },
  {
    action: "filter",
    keys: ["f", "F"],
    display: ["F"],
    description: "Filter",
    group: "view",
    surfaces: { grid: true, leaderboard: true, results: true },
  },
  {
    // Not on leaderboard (C = compare selected) nor summary (C = copy
    // link): there Columns is its button, the header menu and ⌘K.
    action: "columns",
    keys: ["c", "C"],
    display: ["C"],
    description: "Show, hide, freeze and reorder columns",
    group: "view",
    surfaces: { grid: "Columns", list: "Columns", results: "Columns" },
  },
  {
    action: "history",
    keys: ["h", "H"],
    display: ["H"],
    description: "Version history",
    group: "view",
    surfaces: { grid: true },
  },
  {
    action: "collapseAll",
    keys: ["x", "X"],
    display: ["X"],
    description: "Collapse all fields",
    group: "view",
    surfaces: { row: true },
  },
  {
    action: "expandAllSteps",
    keys: ["e", "E"],
    display: ["E"],
    description: "Expand all steps",
    group: "view",
    surfaces: { taskCompare: true, summary: "Expand all" },
  },
  {
    action: "collapseAllSteps",
    keys: ["shift+e"],
    display: ["⇧E"],
    description: "Collapse all steps",
    group: "view",
    surfaces: { taskCompare: true, summary: "Collapse all" },
  },
  {
    action: "toggleGroup",
    keys: ["e", "E"],
    display: ["E"],
    description: "Expand or collapse the group",
    group: "view",
    surfaces: { leaderboard: true },
  },
  {
    action: "full",
    keys: ["f", "F"],
    display: ["F"],
    description: "Expand or restore the panel",
    group: "view",
    surfaces: { row: true, drawer: true },
  },
  // ------------------------------------------------------------ act
  {
    action: "select",
    keys: ["x", "X"],
    display: ["X"],
    description: "Select",
    group: "act",
    surfaces: {
      list: true,
      grid: "Select row",
      leaderboard: "Select run",
      results: "Select row",
    },
  },
  // IT-R5 G3 (P3): Shift+X selects every row the filter shows.
  {
    action: "selectAll",
    keys: ["shift+x"],
    display: ["Shift", "X"],
    description: "Select all filtered rows",
    group: "act",
    surfaces: { results: true },
  },
  {
    action: "newDataset",
    keys: ["n", "N"],
    display: ["N"],
    description: "New dataset",
    group: "act",
    surfaces: { list: true },
  },
  {
    action: "notes",
    keys: ["n", "N"],
    display: ["N"],
    description: "Notes",
    group: "act",
    surfaces: { review: true },
  },
  {
    action: "undo",
    keys: ["z", "Z"],
    display: ["Z"],
    description: "Undo the last vote",
    group: "act",
    surfaces: { review: true },
  },
  {
    action: "edit",
    keys: ["e", "E"],
    display: ["E"],
    description: "Edit the focused field",
    group: "act",
    surfaces: { row: true, grid: "Edit cell" },
  },
  {
    action: "makeBaseline",
    keys: ["b", "B"],
    display: ["B"],
    description: "Make baseline",
    group: "act",
    surfaces: {
      matrix: "Make this column the baseline",
      taskCompare: "Make the focused lane the baseline",
      leaderboard: "Pin as baseline",
    },
  },
  {
    action: "compareSelected",
    keys: ["c", "C"],
    display: ["C"],
    description: "Compare selected runs",
    group: "act",
    surfaces: { leaderboard: true },
  },
  {
    action: "comments",
    keys: ["c", "C"],
    display: ["C"],
    description: "Comments",
    group: "act",
    surfaces: { review: true },
  },
  {
    action: "markViewed",
    keys: ["v", "V"],
    display: ["V"],
    description: "Mark task viewed",
    group: "act",
    surfaces: { taskCompare: true },
  },
  {
    action: "addNote",
    keys: ["a", "A"],
    display: ["A"],
    description: "Add a note",
    group: "act",
    surfaces: { taskCompare: true },
  },
  {
    action: "addToQueue",
    keys: ["a", "A"],
    display: ["A"],
    description: "Add to annotation queue",
    group: "act",
    surfaces: { row: true },
  },
  {
    action: "falcon",
    keys: ["f", "F"],
    display: ["F"],
    description: "Ask Falcon about this page",
    group: "act",
    surfaces: { summary: true },
  },
  {
    action: "copyRowId",
    keys: ["shift+c"],
    display: ["⇧C"],
    description: "Copy row id",
    group: "act",
    surfaces: { row: true },
  },
  {
    action: "copyOutput",
    keys: ["shift+c"],
    display: ["⇧C"],
    description: "Copy output",
    group: "act",
    surfaces: { drawer: true, results: true },
  },
  {
    action: "copyLink",
    keys: ["c", "C"],
    display: ["C"],
    description: "Copy link",
    group: "act",
    surfaces: {
      row: true,
      drawer: true,
      taskCompare: "Copy link (task + step)",
      summary: true,
      // L7c g1 (sheet:shortcuts): with no row open, C copies the page's
      // link (the open row's link is the drawer's C).
      results: "Copy link to this view",
    },
  },
  // ------------------------------------------------------------ R4 (lane F)
  // R4-SPEC D18, D25, brief §I5. Several share a key with an R3 action on
  // the same surface (I, 1–4, alt+←): useShortcuts fires the first one the
  // screen has a live handler for (actionsForEvent).
  {
    action: "graphView",
    keys: ["g", "G"],
    display: ["G"],
    description: "Graph view",
    group: "view",
    surfaces: { taskCompare: true, drawer: true, paths: "Graph of the paths" },
  },
  {
    action: "timelineView",
    keys: ["l", "L"],
    display: ["L"],
    description: "Timeline view",
    group: "view",
    surfaces: { taskCompare: true, drawer: true },
  },
  {
    action: "inspectNode",
    keys: ["i", "I"],
    display: ["I"],
    description: "Open the selected node's spans for both runs",
    group: "view",
    surfaces: { taskCompare: true, paths: "Inspect the selected step" },
  },
  {
    action: "rerunChanged",
    keys: ["w", "W"],
    display: ["W"],
    description: "Rerun the failed tries with the new code",
    group: "act",
    surfaces: { results: true, leaderboard: true, matrix: true, drawer: true },
  },
  {
    action: "rerunWithChanges",
    keys: ["shift+r"],
    display: ["⇧R"],
    description: "Rerun with changes…",
    group: "act",
    surfaces: {
      leaderboard: true,
      results: true,
      matrix: true,
      taskCompare: true,
      drawer: true,
    },
  },
  {
    action: "lensPlay",
    keys: [" "],
    display: ["Space"],
    description: "Play or pause (lens focused or under the pointer)",
    group: "view",
    surfaces: { taskCompare: true },
  },
  {
    action: "prevTurn",
    keys: ["<"],
    display: ["<"],
    description: "Previous turn",
    group: "navigate",
    surfaces: { taskCompare: true },
  },
  {
    action: "nextTurn",
    keys: [">"],
    display: [">"],
    description: "Next turn",
    group: "navigate",
    surfaces: { taskCompare: true },
  },
  {
    action: "blink",
    keys: ["q", "Q"],
    display: ["Q"],
    description: "Blink between the two images",
    group: "view",
    surfaces: { taskCompare: true },
  },
  {
    action: "pickSource",
    // L7c g2 (picker:keys-breadcrumb): step 1 has three cards.
    keys: ["1", "2", "3"],
    display: ["1–3"],
    description: "Pick a way to run (1–3)",
    group: "act",
    surfaces: { newExperiment: true },
  },
  {
    action: "catalogBack",
    keys: ["Backspace", "alt+ArrowLeft"],
    display: ["⌫", "⌥←"],
    description: "Back one level",
    group: "navigate",
    surfaces: { newExperiment: true },
  },
  {
    action: "addToDataset",
    keys: ["a", "A"],
    display: ["A"],
    description: "Add the selected rows (or the cursor row) to a dataset",
    group: "act",
    surfaces: { results: true },
  },
  // ------------------------------------------------------------ act (end)
  {
    action: "escape",
    keys: ["Escape"],
    display: ["Esc"],
    description: "Step back: full, then split, then closed",
    group: "navigate",
    surfaces: {
      row: true,
      drawer: true,
      review: "Exit",
      global: "Close",
      grid: "Clear selection",
      taskCompare: "Back to where you came from",
    },
  },
  {
    action: "help",
    keys: ["?"],
    display: ["?"],
    description: "Keyboard shortcuts",
    group: "act",
    surfaces: { row: true, drawer: true, review: true, global: true },
  },
  {
    action: "search",
    keys: ["/"],
    display: ["/"],
    description: "Focus search",
    group: "navigate",
    surfaces: {
      experiments: true,
      results: true,
      global: "Search on this page",
      list: true,
      grid: true,
      leaderboard: true,
      taskCompare: "Search in lanes",
    },
  },
];

const ARROWS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]);

const BY_ACTION = Object.fromEntries(KEYMAP.map((e) => [e.action, e]));

/** The KEYMAP entry for an action, or null. */
export const keymapEntry = (action) => BY_ACTION[action] ?? null;

const modSymbol = () => (IS_MAC ? "⌘" : "Ctrl+");

/** A display cap for this platform: `⌘K` → `Ctrl+K`, `⌥←` → `Alt+←`. */
export const localiseCap = (cap) => {
  if (IS_MAC) return cap;
  return cap
    .replace(/^⌘/, "Ctrl+")
    .replace(/^⌥/, "Alt+")
    .replace(/^⇧/, "Shift+");
};
const localise = localiseCap;

const CAP_NAMES = {
  Enter: "↵",
  Escape: "Esc",
  ArrowLeft: "←",
  ArrowRight: "→",
  ArrowUp: "↑",
  ArrowDown: "↓",
  " ": "Space",
};

/**
 * A key string as a cap: `mod+k` → `⌘K`, `shift+j` → `⇧J`, `Escape` → `Esc`.
 * For KeyHintTooltip `keys`.
 */
export function capForKey(key) {
  const s = String(key ?? "");
  const m = /^(mod|shift|alt)\+(.+)$/.exec(s);
  const base = (k) => CAP_NAMES[k] ?? (k.length === 1 ? k.toUpperCase() : k);
  if (!m) return localise(base(s));
  const prefix = { mod: "⌘", shift: "⇧", alt: "⌥" }[m[1]];
  return localise(`${prefix}${base(m[2])}`);
}

/**
 * Key caps for tooltips: "J", "⌘↵" (Ctrl+↵ off macOS).
 * @param {string} action
 * @param {{all?: boolean}} [opts] all: every cap joined, "J / →"
 */
export function keyLabel(action, { all = false } = {}) {
  const entry = BY_ACTION[action];
  if (!entry) return "";
  const caps = entry.display.map(localise);
  return all ? caps.join(" / ") : caps[0];
}

/** "Next row (J)" for a tooltip, using the surface description if any. */
export function withKeyHint(text, action) {
  const cap = keyLabel(action);
  return cap ? `${text} (${cap})` : text;
}

/**
 * Rows for ShortcutSheet: [{action, caps[], description, group}] for a
 * surface.
 */
export function shortcutRows(surface) {
  return KEYMAP.filter((e) => liveOn(e, surface)).map((e) => ({
    action: e.action,
    caps: e.display.map(localise),
    description:
      typeof e.surfaces[surface] === "string"
        ? e.surfaces[surface]
        : e.description,
    group: e.group ?? "act",
  }));
}

/** Candidate key strings for an event, most specific first. */
export function eventKeyNames(event) {
  if (!event) return [];
  const k = event.key ?? "";
  const mod = event.metaKey || event.ctrlKey;
  if (event.altKey) return mod ? [] : [`alt+${k}`];
  if (mod) {
    const lower = k.length === 1 ? k.toLowerCase() : k;
    return lower === k ? [`mod+${k}`] : [`mod+${k}`, `mod+${lower}`];
  }
  if (event.shiftKey && /^[a-zA-Z]$/.test(k))
    return [`shift+${k.toLowerCase()}`, k];
  return [k];
}

/** True when `event` is one of the keys bound to `action`. */
export function matchesAction(event, action) {
  const entry = BY_ACTION[action];
  if (!entry || !event) return false;
  return eventKeyNames(event).some((name) => entry.keys.includes(name));
}

/**
 * The action an event triggers on a surface, or null.
 * Cmd/Ctrl keys only match `mod+` bindings; Alt only `alt+` bindings;
 * Shift+letter prefers a `shift+x` binding on the surface, else the plain
 * key (so Shift+J is still `next` where nothing binds `shift+j`).
 * @param {KeyboardEvent} event
 * @param {string} [surface] limit to actions that exist on this surface
 */
export function actionForEvent(event, surface) {
  const names = eventKeyNames(event);
  for (const name of names) {
    const hit = KEYMAP.find((e) => liveOn(e, surface) && e.keys.includes(name));
    if (hit) return hit.action;
  }
  return null;
}

/**
 * Every action an event can trigger on a surface, most specific key name
 * first, then KEYMAP order (R4: useShortcuts fires the first with a live
 * handler, so `I` is `inspect` or `inspectNode` by what the screen binds).
 * @param {KeyboardEvent} event
 * @param {string} [surface]
 * @returns {string[]}
 */
export function actionsForEvent(event, surface) {
  const out = [];
  eventKeyNames(event).forEach((name) =>
    KEYMAP.forEach((e) => {
      if (liveOn(e, surface) && e.keys.includes(name))
        if (!out.includes(e.action)) out.push(e.action);
    }),
  );
  return out;
}

/** True for arrow-key events (they never fire from the grid or a text field). */
export const isArrowKey = (event) => ARROWS.has(event?.key);

export const MOD_KEY_LABEL = modSymbol();
