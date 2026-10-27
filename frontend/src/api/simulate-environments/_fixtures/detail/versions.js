// Vel's environment version history for his voice support line, copy without
// em dashes. Shown only with ?uiSample=versions: our API has no environment
// versions yet.
export const SAMPLE_ENV_VERSIONS = [
  {
    label: "v3",
    latest: true,
    summary:
      "World: Seeded the refusal paths so the scenarios that should be declined have something to be declined against · Scenario checks rewritten",
    kinds: "World + contract + Sub-goals",
    runs: 2,
    ago: "2d ago",
  },
  {
    label: "v2",
    summary:
      "Contract: Moved a rule out of the prompt and into the world, so breaking it now fails rather than reads badly",
    kinds: "World + contract",
    runs: 0,
    ago: "11d ago",
  },
  {
    label: "v1",
    summary: "First build, read from the agent source",
    kinds: "First build",
    runs: 0,
    ago: "24d ago",
  },
];

export const SAMPLE_CURRENT_ENV_VERSION = "v3";
export const SAMPLE_NEXT_ENV_VERSION = "v4";

export const SAMPLE_PENDING_EDITS = {
  count: 2,
  detail: "Caller audio quality · shipment_status switched to Stateful",
  cost: "Comparable with v3 on the scenarios that don't call shipment_status",
};

// What rolling back to an older version does (Vel's rollback plan for v2).
export const SAMPLE_ROLLBACK_LINES = {
  v2: [
    "Tool behaviour, actors and seed stay as they are. That version didn't keep them.",
    "40 scenarios proved only on newer versions will be marked Re-check",
  ],
  v1: [
    "Tool behaviour, actors and seed stay as they are. That version didn't keep them.",
    "52 scenarios proved only on newer versions will be marked Re-check",
  ],
};

export const SAMPLE_AGENT_DEFAULT = "Default: production (v4)";

export const SAMPLE_AGENT_VERSIONS = [
  {
    label: "v1",
    runs: 2,
    ago: "just now",
    note: "First version connected today.",
  },
  {
    label: "v4",
    testing: true,
    runs: 0,
    ago: "yesterday",
    note: "From Improvement 4 · Vapi copy.",
  },
  {
    label: "v3",
    runs: 0,
    ago: "4d ago",
    note: "From Improvement 3 · Vapi copy.",
  },
  {
    label: "v2",
    runs: 0,
    ago: "34d ago",
    note: "From Improvement 2 · Vapi copy.",
  },
];
