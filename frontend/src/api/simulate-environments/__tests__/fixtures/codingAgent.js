// Contract-true answers of the coding-agent routes (simulate/serializers/agent_fix.py:
// AgentFixCodingAgentBriefSerializer, AgentFixAttemptVerdictSerializer), with the
// held-back cases of the run they belong to. codingAgent.contract.test.js checks each
// one against the generated OpenAPI contract.

export const SOURCE_RUN_ID = "7d3f5c2a-1b4e-4c8d-9a6f-2e1d0c9b8a71";
export const ATTEMPT_ID = "3c9e1f7b-5a2d-4e8c-b1f6-9d0a7e3c2b54";

// The run's cases the platform held back: never in the brief, the verdict or the copy.
export const HELD_BACK_KEYS = ["late-cancel-held-1", "late-cancel-held-2"];

// What simulate/selectors/agent_fix_outside.py `_instructions` writes for this run.
const INSTRUCTIONS = [
  "# Fix the failed run on Pickup dispatch",
  "",
  "Future AGI ran your agent on its test cases, and the cases below failed. Change the agent so that they pass, and keep the cases that pass today passing. Future AGI then checks your change as an Epoch and gives a verdict.",
  "",
  "## What failed",
  "",
  "- **Late cancellation** (`late-cancel`): did not meet escalated.",
  "  - escalated: The agent refunded without asking a supervisor.",
  "",
  `Each failed conversation is in the brief's \`failures\`. To read them, run \`fagi epoch brief --run ${SOURCE_RUN_ID} --json\`.`,
  "",
  "## Rules for your change",
  "",
  "- Change the agent's files that the brief lists in `files`. You can add new files. A file you leave out of the tree is deleted.",
  "- Do not change a locked file. Future AGI refuses an attempt that does.",
  "- Keep the change under 5 MB as a patch.",
  "",
  "## Submit your fix",
  "",
  "```sh",
  `fagi epoch submit --run ${SOURCE_RUN_ID} --summary "<what you changed and why>"`,
  "fagi epoch verdict <attempt-id> --wait",
  "```",
  "",
  "A run takes at most 4 attempts. This run has 3 left. Each attempt runs the agent on the run's cases and uses credits. A verdict can take tens of minutes.",
  "",
  "The command is `fagi`. To install it, run `npm install -g @future-agi/cli`.",
  "",
  "## Held-back cases",
  "",
  "Do not read held-back cases or any held-back field of an Epoch. Future AGI checks your fix on cases you were not given.",
  "",
].join("\n");

export const fixtureBrief = (overrides = {}) => ({
  source_run: {
    id: SOURCE_RUN_ID,
    number: 4,
    state: "completed",
    finished_at: "2026-11-20T09:12:00Z",
  },
  environment: {
    id: "5b8a2c1d-3e4f-4a6b-8c7d-1e2f3a4b5c6d",
    name: "Pickup dispatch",
    version: 3,
  },
  agent: {
    name: "Pickup dispatch agent",
    kind: "archive",
    version_id: "9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d",
    version_number: 8,
    ref: null,
  },
  base_commit: "",
  files: ["agent.py", "prompts/system.md"],
  targets: [
    {
      key: "late-cancel",
      name: "Late cancellation",
      failing_sub_goals: ["escalated"],
    },
  ],
  protected_scenario_keys: ["on-time-cancel"],
  locked_paths: [],
  failures: [
    {
      scenario_key: "late-cancel",
      trial: 1,
      sub_goals: { escalated: false },
      reasons: { escalated: "The agent refunded without asking a supervisor." },
      transcript: "user: I need to cancel my pickup.\nagent: Done, refunded.",
    },
  ],
  attempts: { used: 1, max: 4 },
  blocked: null,
  instructions: INSTRUCTIONS,
  ...overrides,
});

// Each refusal the brief can carry, with the words selectors/agent_fix_outside.py gives.
export const BLOCKED_BRIEFS = {
  fix_attempts_used:
    "This run has had its 4 attempts. Run the environment again, then fix the new run.",
  fix_attempt_too_large:
    "An attempt on this run could need 61,200 credits, more than the 50,000 an Epoch may use. Run the environment on fewer scenarios, then fix the new run.",
  fix_attempt_scope_invalid:
    "This run has more cases or models than an attempt can check. Run the environment on fewer scenarios, then fix the new run.",
  fix_targets_passed:
    "Nothing failed in this run, so there is nothing to fix. Pick a run with failing cases.",
};

export const fixtureBlockedBrief = (code = "fix_attempts_used") =>
  fixtureBrief({
    attempts: { used: code === "fix_attempts_used" ? 4 : 0, max: 4 },
    blocked: { code, message: BLOCKED_BRIEFS[code] },
  });

// The engine's own words on the checks the coding agent may read: the page never
// shows them, only its plain line for each status.
export const ENGINE_STATIC_DETAIL = "2 files changed, 14 lines; parses";
export const ENGINE_TARGET_DETAIL = "pass rate 0.33 -> 1.00, p=0.004";

export const fixtureVerdict = (overrides = {}) => ({
  id: ATTEMPT_ID,
  number: 2,
  state: "no_improvement",
  step: "done",
  ending: "no_improvement",
  failure: null,
  summary: "No candidate passed every tier.",
  tiers: [
    { name: "static", status: "passed", detail: ENGINE_STATIC_DETAIL },
    { name: "target", status: "passed", detail: ENGINE_TARGET_DETAIL },
    { name: "held-back", status: "failed", detail: "" },
    { name: "must-pass", status: "passed", detail: "" },
    { name: "repeats", status: "skipped", detail: "" },
  ],
  must_pass_regressions: [],
  patch_available: true,
  pull_request_url: "",
  ...overrides,
});

// The attempt's Epoch as a signed-in read gives it: the held-back cases named, and
// its candidate's verdict with the held-back pass rate.
export const outsideAttemptFix = (base) => ({
  ...base,
  id: ATTEMPT_ID,
  number: 7,
  source_run_id: SOURCE_RUN_ID,
  attempt_of_id: "1f2e3d4c-5b6a-4978-8a6b-5c4d3e2f1a0b",
  state: "no_improvement",
  step: "done",
  request: {
    ...base.request,
    held_back_scenario_keys: HELD_BACK_KEYS,
    outside_candidate: {
      summary: "Ask a supervisor before refunding a late cancellation.",
      number: 2,
    },
  },
});
