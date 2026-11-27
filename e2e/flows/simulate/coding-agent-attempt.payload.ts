// Stubbed responses for SIM-E2E-043, in the agent-fix-runs contracts' shapes, and
// the words the flow expects the page to show for them. No imports, so both suites
// can read it: the flow serves these from the browser, and
// frontend/src/api/simulate-environments/__tests__/codingAgentFlowStubs.test.ts parses
// every one with the generated zod (frontend/src/generated/api-contracts/api.zod.ts)
// and checks the expected words against the page's own helpers, so neither can drift.

type Json = Record<string, unknown>;

// The prebuilt template env-voice-support: the workspace opens it without a built
// environment, so the epoch page renders (as SIM-E2E-007/008/041 use it). The
// contract's environment ids are UUIDs; the frontend parse swaps this one id.
export const ENV_ID = 'env-voice-support';
const ENV_NAME = 'Customer Support Line';
const AGENT_VERSION_ID = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d';
const CANDIDATE_VERSION_ID = '2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e';

// The attempt's Epoch, the finished run it fixes, the first attempt on that run, and
// the coding agent's change as the Epoch's one candidate: UUIDs the flow mints.
export type Ids = {
  attempt: string;
  sourceRun: string;
  attemptOf: string;
  candidate: string;
};

// The coding agent's second attempt on the run, and its own note on the change
// (request.outside_candidate, from fagi epoch submit --summary).
export const ATTEMPT_NUMBER = 2;
export const ATTEMPT_TITLE = `Attempt ${ATTEMPT_NUMBER} from your coding agent`;
export const AGENT_NOTE = 'Ask for the order date before offering a refund.';

// The run's failing cases the coding agent was given, and the ones Future AGI held
// back from it. A signed-in read names the held-back ones; the attempt's panel never.
const TARGET_KEY = 'refund-after-window';
export const HELD_BACK_KEYS = ['refund-held-1', 'refund-held-2'];

// The engine's words on the two checks the coding agent may read. The verdict read
// carries them; the page shows only its plain line for each status.
export const ENGINE_STATIC_DETAIL = '1 file changed, 6 lines; parses';
export const ENGINE_TARGET_DETAIL = 'pass rate 0.00 -> 1.00, p=0.004';

// The attempt's checks as the panel lists them, in the verdict read's order: each
// row's data-check (CheckLine.jsx), its label, and what it says. The repeats check
// never ran, because held-back failed before it.
export const CHECK_ROWS = [
  { check: 'passed', label: 'Change is valid', says: 'Applies cleanly and changes no locked file.' },
  { check: 'passed', label: 'Target scenarios', says: 'A clear gain on the failing cases your agent was given.' },
  { check: 'failed', label: 'Held-back scenarios', says: "Did not pass on cases your agent wasn't given." },
  { check: 'passed', label: 'Must-pass scenarios', says: 'No case that passed before broke.' },
  { check: 'not-run', label: 'Holds over repeats', says: 'Not run: an earlier check failed (Held-back scenarios)' },
];

// The ending's sentence for an attempt with no safe improvement (attemptEndingText in
// improvements/fixRunView.js): the next step is the coding agent's.
export const ENDING_SENTENCE =
  "Your coding agent's change did not pass every check, so nothing changed. The checks above say which. Fix the change, then submit it again with fagi epoch submit.";

// What the snackbar says once the prompt is on the clipboard (useCopyForCodingAgent.ts).
export const COPIED = 'Copied. Paste it into your coding agent.';

const request = (): Json => ({
  target_scenario_keys: [TARGET_KEY],
  held_back_scenario_keys: HELD_BACK_KEYS,
  failing_sub_goals: ['checked_window'],
  surfaces: ['prompt'],
  locked_paths: [],
  budget_credits: 1500,
  max_minutes: 60,
  outside_candidate: { summary: AGENT_NOTE, number: ATTEMPT_NUMBER },
});

const tier = (name: string, status: string, detail = ''): Json => ({ name, status, detail });

// GET agent-fix-runs/<id>/: the attempt's Epoch, ended with no safe improvement,
// as a signed-in read gives it: its one candidate is the coding agent's change,
// rejected at held-back, with the held-back pass rate.
export function attemptEpoch(ids: Ids): Json {
  return {
    id: ids.attempt,
    number: 9,
    environment_id: ENV_ID,
    environment: { name: ENV_NAME, version: 1, default_agent_version_id: AGENT_VERSION_ID },
    source_run_id: ids.sourceRun,
    source_run_number: 7,
    base_agent_version_id: AGENT_VERSION_ID,
    agent: { name: 'Support agent', kind: 'archive', version_id: AGENT_VERSION_ID, version_number: 1, ref: null },
    state: 'no_improvement',
    stage: 'Done',
    step: 'done',
    plan: { target: 1, held_back: 2, must_pass: 1, protected: 1, max_candidates: 1, repeats: 3, baseline_spread_pts: 10 },
    diagnosis: null,
    request: request(),
    spent_credits: 320,
    committed_credits: 320,
    // An ending with no winner: the engine's report names none.
    report: { ending: 'no_improvement', summary: 'No candidate passed every tier.', winner_candidate_id: null },
    failure: null,
    retried_from_id: null,
    retryable: false,
    attempt_of_id: ids.attemptOf,
    counted: true,
    limited_validation: false,
    issue: null,
    from_issue: false,
    flaky_protected_text: [],
    check_runs: [],
    candidates: [
      {
        id: ids.candidate,
        attempt: 1,
        agent_version_id: CANDIDATE_VERSION_ID,
        version_number: 2,
        summary: AGENT_NOTE,
        outcome: 'rejected',
        verdict: {
          accepted: false,
          reason: 'held-back: not a clear gain',
          target_pass_rate: [0, 1],
          held_back_pass_rate: [0.5, 0.5],
          must_pass_regressions: [],
          must_pass_within_noise: [],
          tiers: [
            tier('static', 'passed', ENGINE_STATIC_DETAIL),
            tier('target', 'passed', ENGINE_TARGET_DETAIL),
            tier('held-back', 'failed', '50% -> 50%'),
            tier('must-pass', 'passed', '1 of 1 kept'),
            tier('repeats', 'skipped'),
          ],
        },
        check_runs: [],
        pull_request_url: '',
      },
    ],
  };
}

// GET agent-fix-runs/<id>/verdict/: the same attempt as its coding agent reads it.
// Detail only on the static and target checks; nothing about a held-back case.
export function attemptVerdict(ids: Ids): Json {
  return {
    id: ids.attempt,
    number: ATTEMPT_NUMBER,
    state: 'no_improvement',
    step: 'done',
    ending: 'no_improvement',
    failure: null,
    summary: '',
    tiers: [
      tier('static', 'passed', ENGINE_STATIC_DETAIL),
      tier('target', 'passed', ENGINE_TARGET_DETAIL),
      tier('held-back', 'failed'),
      tier('must-pass', 'passed'),
      tier('repeats', 'skipped'),
    ],
    counted: true,
    must_pass_regressions: [],
    patch_available: true,
    pull_request_url: '',
  };
}

// The brief's instructions, as simulate/selectors/agent_fix_outside.py writes them,
// shortened. They name the minted source run, so the copy can only be this brief's.
export const instructions = (ids: Ids): string =>
  [
    `# Fix the failed run on ${ENV_NAME}`,
    '',
    'Future AGI ran your agent on its test cases, and the cases below failed. Change the agent so that they pass, and keep the cases that pass today passing.',
    '',
    '## Submit your fix',
    '',
    '```sh',
    `fagi epoch submit --run ${ids.sourceRun} --summary "<what you changed and why>"`,
    'fagi epoch verdict <attempt-id> --wait',
    '```',
    '',
    'A run takes at most 4 attempts. This run has 2 left.',
    '',
  ].join('\n');

// GET agent-fix-runs/coding-agent-brief/?source_run_id=<run>: an open brief (nothing
// blocks another attempt) after the two attempts so far.
export function codingAgentBrief(ids: Ids): Json {
  return {
    source_run: { id: ids.sourceRun, number: 7, state: 'completed', finished_at: '2026-11-25T10:05:00Z' },
    environment: { id: ENV_ID, name: ENV_NAME, version: 1 },
    agent: { name: 'Support agent', kind: 'archive', version_id: AGENT_VERSION_ID, version_number: 1, ref: null },
    base_commit: '',
    files: ['prompts/system.md'],
    targets: [{ key: TARGET_KEY, name: 'Refund asked for 40 days after delivery', failing_sub_goals: ['checked_window'] }],
    protected_scenario_keys: ['refund-in-window'],
    locked_paths: [],
    failures: [
      {
        scenario_key: TARGET_KEY,
        trial: 1,
        sub_goals: { checked_window: false },
        reasons: { checked_window: 'The agent refunded without checking the order date.' },
        transcript: 'user: I want a refund.\nagent: Done, refunded.',
      },
    ],
    attempts: { used: 2, max: 4 },
    blocked: null,
    instructions: instructions(ids),
  };
}

// What Copy for coding agent puts on the clipboard: the setup (codingAgentSetup in
// frontend/src/api/simulate-environments/codingAgent.ts), then the brief's
// instructions exactly. The setup links the app's MCP server settings page.
export const copiedPrompt = (ids: Ids, origin: string): string =>
  [
    '## Set up Future AGI first',
    '',
    'Skip this if the `fagi` command already works here.',
    '',
    '1. Install the Future AGI CLI: `npm install -g @future-agi/cli`. To run it without installing, use `npx @future-agi/cli` in place of `fagi` in each command.',
    '2. Sign in: `fagi login`.',
    `3. Optional: add the Future AGI MCP server to your coding agent, so it can read this brief and the verdict as tools. The steps for Claude Code, Cursor and Codex are at ${origin}/dashboard/settings/mcp-server.`,
    '',
    '---',
    '',
    instructions(ids),
  ].join('\n');
