// Copy + mock constants for the Agents tab (the "Manage versions" surface).
//
// The version helpers narrate what the platform would do when a source agent's
// version changes — re-read the agent, regenerate the contract, re-derive
// scenarios, re-run evals. Until real re-derivation lands, that narration reads
// from the MOCK_* placeholders below; they're grouped and named as mock so the
// live pipeline can find and replace them in one pass.

// Header copy for the composed Agents surface (the "Manage versions" drawer).
export const AGENTS_PANEL_COPY = {
  title: "Agent",
  subtitle:
    "One agent per environment. New versions re-derive its contract, scenarios and evaluations.",
};

// Fallback phrases the step builders drop in when the agent, a version, or a
// reach (endpoint / location) is missing — so the narration still reads as a
// sentence instead of "undefined".
export const STEP_FALLBACKS = {
  agent: "the agent",
  previousVersion: "the previous version",
  versionEndpoint: "the version's endpoint",
  attachedAgent: "the attached agent",
  previousSource: "the previous source",
  newSource: "the new source",
  attachedSource: "the attached source",
};

// Placeholder derivation results the scripted builder turns display. Grouped by
// flow (version switch / version upgrade / promote to source). Numbers and diffs
// are illustrative, not computed.
export const MOCK_DERIVATION = {
  switch: {
    toolsResult: "12 tools · 1 signature differs from previous",
    rulesResult: "5 rules · unchanged",
    scenariosResult: "88 kept · 2 archived (no longer solvable in this version)",
    evalsResult: "no changes",
  },
  upgrade: {
    toolsResult: "12 tools · 1 changed",
    rulesResult: "5 rules · no changes",
    diff: { tools_added: 0, tools_removed: 0, tool_signatures_changed: 1, rules_changed: 0 },
    scenariosResult: "88 kept · 2 archived (no longer solvable)",
    evalsResult: "no changes",
  },
  promote: {
    toolsResult: "12 tools",
    rulesResult: "5 rules",
    diff: { tools_added: 1, tools_removed: 0, rules_changed: 1 },
    scenariosResult: "88 kept · 3 archived (no longer solvable)",
    evalsResult: "no changes",
  },
};
