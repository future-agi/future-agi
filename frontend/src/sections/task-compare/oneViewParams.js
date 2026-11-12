// Side by side · one view (ONE-VIEW-SPEC §7, A4-02): there is no Story /
// Steps / Raw depth and no separate detail switch. Old links still work:
// `depth=steps` → Level `Calls` with every step open; `depth=raw` → the
// Inspect panel on `Timeline` (or the old `rtab`); `depth=story` and
// the old `detail` flag → the default; `dock=answers|checks` → that Inspect tab. Each
// mapped param is then removed (replace). `ui=` is scrubbed globally by G's
// LegacyParamScrub. Pure; tested in __tests__/oneView.test.js.

/** Inspect panel tabs, in strip order (Workspace only with a harness lane). */
export const INSPECT_TABS = Object.freeze([
  // IT-R5 G4 P4: Inspect opens on what each run used (model and tools).
  {
    value: "setup",
    label: "Setup",
    tip: "Setup — each run's model, framework, version, tools and MCP servers",
  },
  {
    value: "answers",
    label: "Answers",
    tip: "Answers — each lane's final answer side by side",
  },
  {
    value: "checks",
    label: "Checks",
    tip: "Checks — every check with its evidence",
  },
  {
    value: "timeline",
    label: "Timeline",
    tip: "Timeline — every lane's steps on one time axis",
  },
  {
    value: "tools",
    label: "Tool calls",
    tip: "Tool calls — each tool's calls, errors and arguments per lane",
  },
  {
    value: "prompts",
    label: "Prompts",
    tip: "Prompts — the prompt sent at each model call",
  },
  {
    value: "workspace",
    label: "Workspace",
    tip: "Workspace — files changed and the terminal (harness lanes)",
    harnessOnly: true,
  },
  {
    value: "scores",
    label: "Scores",
    tip: "Scores — judge and check evidence",
  },
  { value: "json", label: "JSON", tip: "JSON — the raw trial" },
]);
export const INSPECT_VALUES = Object.freeze(INSPECT_TABS.map((t) => t.value));

/** The tabs a lane set shows (Workspace only when a lane is a harness). */
export const inspectTabsFor = (lanes = []) =>
  INSPECT_TABS.filter(
    (t) => !t.harnessOnly || lanes.some((l) => l?.kind === "harness"),
  );

/** Old Raw tab names → Inspect tabs (Waterfall is now Timeline). */
const RTAB_TO_INSPECT = {
  waterfall: "timeline",
  tools: "tools",
  prompts: "prompts",
  workspace: "workspace",
  scores: "scores",
  json: "json",
};

/** R4 depths that live in the URL (`depth=graph|call`); the R3 depths map. */
export const R4_DEPTHS = Object.freeze(["graph", "call"]);
/** `depth=` from the URL when it is an R4 depth (else null). */
export const depthOf = (value) => (R4_DEPTHS.includes(value) ? value : null);

/** `inspect=` from the URL, validated (null = closed). */
export function inspectOf(value) {
  return INSPECT_VALUES.includes(value) ? value : null;
}

/**
 * The URL patch that turns an old Side by side link into the one view.
 * @param {URLSearchParams|string} search
 * @returns {{patch: object, expandAll: boolean} | null}  null = nothing to
 *   map; `patch` uses useTaskCompareState.set keys (null removes a param)
 */
export function legacyParamPatch(search) {
  const sp =
    search instanceof URLSearchParams
      ? search
      : new URLSearchParams(search ?? "");
  const depth = sp.get("depth");
  const dock = sp.get("dock");
  const rtab = sp.get("rtab");
  const oldDock = dock === "answers" || dock === "checks";
  const keepDepth = R4_DEPTHS.includes(depth);
  if (
    (!sp.has("depth") || keepDepth) &&
    !sp.has("detail") &&
    !sp.has("rtab") &&
    !oldDock
  )
    return null;
  const patch = {
    ...(keepDepth ? {} : { depth: null }),
    detail: null,
    rtab: null,
  };
  let expandAll = false;
  if (depth === "steps") {
    const level = sp.get("level");
    if (!level || level === "milestones") patch.level = "calls";
    expandAll = true;
  }
  if (depth === "raw") patch.inspect = RTAB_TO_INSPECT[rtab] ?? "timeline";
  if (oldDock) {
    patch.dock = null;
    if (!patch.inspect) patch.inspect = dock;
  }
  return { patch, expandAll };
}

/** Outline ids (useOutline, surface `taskCompare`, scope `<row>:<runs>`). */
export const lineId = (rowId, laneId, stepN) => `${rowId}|${laneId}|${stepN}`;
export const gapId = (rowId, laneId) => `gap|${rowId}|${laneId}`;
