// Vel's scenario proof and internals sample (his prototype's proofs,
// admission gates and simulated-caller policy), copy without em dashes. Shown
// in the scenario drawer only with ?uiSample=proof: the API has no proofs,
// caller policy, reference solution or scenario folder yet. Every line is
// generic, so none of it reads as a fact about the real scenario it sits in.

export const SAMPLE_PROOF_VERSION = "v3";

export const SAMPLE_PROOF_NOTE =
  "Proved against the world with a reference solution of tool calls.";

// Staged, Solvable and Not vacuous show inline; the admission gates past them
// open with the details. `ok`: true passed, false failed, null not yet proved.
export const SAMPLE_PROOF_GATES = [
  {
    id: "ready",
    label: "Staged",
    core: true,
    ok: true,
    detail: "The world holds what it presumes.",
    file: "setup.py",
  },
  {
    id: "solvable",
    label: "Solvable",
    core: true,
    ok: true,
    detail: "A reference solution passes every check.",
    file: "reference_solution.json",
  },
  {
    id: "vacuous",
    label: "Not vacuous",
    core: true,
    ok: true,
    detail: "An empty run fails it.",
    file: "checks.py",
  },
  {
    id: "mutation",
    label: "Mutation controls",
    ok: true,
    detail:
      "A wrong action fails the checks, and every check has a passing and a failing demonstration.",
    file: "mutations.py",
  },
  {
    id: "altpaths",
    label: "Alternate valid paths pass",
    ok: true,
    detail:
      "A second valid solution, reached another way, passes too. The checks aren't overfit to one path.",
    file: "alt_paths.py",
  },
  {
    id: "setup",
    label: "Setup can't earn credit",
    ok: true,
    detail:
      "Nothing setup.py writes counts toward a check. Credit is only for the agent's own actions.",
    file: "setup_credit.py",
  },
  {
    id: "exploit",
    label: "Exploit agent scores 0",
    ok: null,
    detail:
      "A scripted agent told to game the test, by claiming success or talking to the judge or the caller, fails it.",
    file: "exploit_agent.py",
  },
];

// How the simulated caller is allowed to behave: a policy, not a prompt.
export const SAMPLE_CALLER_POLICY = {
  facts: [
    "Name and contact as held in the environment",
    "The reference number it is calling about",
    "That it has called about this before",
  ],
  heldBack: [
    {
      fact: "Second identity factor",
      trigger: "Only after the agent asks a second, different question",
    },
    {
      fact: "One-time passcode",
      trigger: "Only after the agent asks for it by name",
    },
    {
      fact: "Full card number",
      trigger: "Never. The caller reads the last four digits only",
    },
  ],
  manner:
    "Normal, medium patience, medium precision · accepts a clear answer the first time (max 2) · waits for the agent to finish",
  termination: [
    "The goal is satisfied and the agent has confirmed it",
    "The agent states a rule that blocks the goal and the caller has pushed back its allowed number of times",
    "The agent transfers or escalates",
    "Six turns with no progress",
  ],
  prohibited: [
    "Volunteering a guarded fact before it is asked for",
    "Contradicting a fact it has already stated",
    "Answering a question with the goal instead of the answer",
    "Accepting an outcome the scenario says it must refuse",
    "Helping the agent past a check the scenario is testing",
  ],
  note: "The caller is never tuned to make the agent pass. It follows this policy, and if that exposes the agent, that is the point.",
};

// The reference solution: proves the scenario can be passed; never run
// against the agent.
export const SAMPLE_REFERENCE_SOLUTION = [
  { tool: "lookup_customer", args: ["phone"] },
  { tool: "verify_identity", args: ["customer_id", "answer"] },
  { tool: "get_account", args: ["customer_id"] },
  { tool: "update_account", args: ["customer_id", "change"] },
  { tool: "respond", args: [] },
];

export const SAMPLE_SCENARIO_FOLDER =
  "environments/support-line/scenarios/sample-scenario/";

export const SAMPLE_SCENARIO_FILES = [
  "scenario.json",
  "setup.py",
  "checks.py",
  "reference_solution.json",
  "mutations.py",
  "alt_paths.py",
  "setup_credit.py",
];
