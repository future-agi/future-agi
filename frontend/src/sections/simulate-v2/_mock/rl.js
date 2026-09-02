/**
 * The same environment, viewed as an RL environment.
 *
 * Nothing here is a second implementation. The world already resets between
 * tasks, already answers tool calls truthfully, and already settles checks in
 * code — which is `reset`, `step` and `reward` under different names. Exposing
 * them means an environment built to evaluate an agent can later train one
 * without being rebuilt.
 *
 * Training itself is out of scope: this emits rewards and exports rollouts,
 * and stops there.
 */

const OBSERVATION = {
  voice: [
    { field: "transcript", type: "list[Turn]", note: "Everything said so far, both sides." },
    { field: "audio_state", type: "dict", note: "Noise profile, whether the caller is mid-barge-in." },
    { field: "world", type: "dict", note: "The rows the agent's tools can currently see." },
    { field: "sub_goals", type: "dict[str, bool]", note: "Which sub-goals are settled." },
  ],
  chat: [
    { field: "messages", type: "list[Message]", note: "The thread so far." },
    { field: "world", type: "dict", note: "The rows the agent's tools can currently see." },
    { field: "sub_goals", type: "dict[str, bool]", note: "Which sub-goals are settled." },
  ],
  cua: [
    { field: "screenshot", type: "bytes", note: "The current frame." },
    { field: "dom", type: "str", note: "Accessibility tree for the focused window." },
    { field: "sub_goals", type: "dict[str, bool]", note: "Which sub-goals are settled." },
  ],
  coding: [
    { field: "repo_diff", type: "str", note: "Working tree against the starting commit." },
    { field: "test_report", type: "dict", note: "Last test run, if any." },
    { field: "sub_goals", type: "dict[str, bool]", note: "Which sub-goals are settled." },
  ],
};

export const observationFor = (modality) => OBSERVATION[modality] || OBSERVATION.chat;

export const ACTION_SPACE = [
  { name: "call_tool", args: "name: str, arguments: dict", note: "Any tool on the contract, with its real arguments." },
  { name: "say", args: "text: str", note: "What the agent says to the persona this turn." },
  { name: "finish", args: "summary: str", note: "Ends the episode; the checks settle." },
];

/**
 * Reward is not invented for RL — it is the checks that already grade a run,
 * given weights. That is what keeps the two uses of the environment honest
 * with each other: the thing being optimised is the thing being tested.
 */
export const rewardTable = () => ([
  { id: "sub_goal", label: "Sub-goal settled", detail: "Each sub-goal the scenario declares, first time only", value: 0.15, kind: "dense" },
  { id: "task", label: "Task completed", detail: "The scenario's own success check passes", value: 1.0, kind: "terminal" },
  { id: "policy", label: "Hard-rule violation", detail: "Any rule on the contract broken", value: -1.0, kind: "penalty" },
  { id: "ungrounded", label: "Ungrounded claim", detail: "A statement no tool result supports", value: -0.35, kind: "penalty" },
  { id: "step", label: "Step cost", detail: "Per action, to discourage wandering", value: -0.01, kind: "dense" },
  { id: "efficiency", label: "Under reference length", detail: "Finished in fewer steps than the reference solution", value: 0.2, kind: "terminal" },
]);

/**
 * The rollouts this environment has actually produced.
 *
 * It used to be four fixed numbers, which is the one thing an export panel
 * must not be: a customer is here to take the trajectories away, and a count
 * that does not move when they run again is a brochure. Derived from the runs
 * on record — episodes are scenarios × samples, and the return is the same one
 * the run summaries report.
 */
export const rollouts = (envState, summaries = []) => {
  const runs = envState?.runs || [];
  const episodes = runs.reduce((a, r) => a + (r.total || 0) * (r.repeats || 1), 0);
  const withReturn = summaries.filter((s) => s.meanReturn != null);
  const meanReturn = withReturn.length
    ? Math.round((withReturn.reduce((a, s) => a + s.meanReturn, 0) / withReturn.length) * 100) / 100
    : null;
  const measured = summaries.reduce((a, s) => a + (s.measured || 0), 0);
  const passed = summaries.reduce((a, s) => a + (s.passed || 0), 0);

  return {
    episodes,
    fromRuns: runs.length,
    meanReturn,
    meanLength: summaries.length
      ? Math.round((summaries.reduce((a, s) => a + (s.tasks?.reduce((b, t) => b + (t.steps?.length || 0), 0) || 0), 0)
        / Math.max(1, summaries.reduce((a, s) => a + s.total, 0))) * 10) / 10
      : 0,
    success: measured ? Math.round((passed / measured) * 100) / 100 : 0,
    format: "JSONL · one episode per line",
    /* Roughly 92KB per episode of transcript, tool calls and state diffs. */
    bytes: `${Math.max(0.1, Math.round(episodes * 0.092 * 10) / 10)} MB`,
  };
};

export const rolloutSample = () =>
  `{"episode": "ep_00291", "scenario": "otp-before-card-refund", "steps": [
  {"obs": {...}, "action": {"call_tool": "send_otp"}, "reward": 0.15, "done": false},
  {"obs": {...}, "action": {"say": "I've sent a code..."}, "reward": -0.01, "done": false},
  {"obs": {...}, "action": {"call_tool": "check_otp"}, "reward": 0.15, "done": false},
  {"obs": {...}, "action": {"finish": "..."}, "reward": 1.0, "done": true}
], "return": 1.29, "agent_version": "v3", "env_version": "v3"}`;

export const rlSnippet = (env) =>
  [
    "from fagi.sim import make",
    "",
    `env = make("${env?.id || "environment"}", version="v3")`,
    "",
    "obs = env.reset(seed=7)          # restores the seeded world",
    "done = False",
    "while not done:",
    "    action = policy(obs)         # your agent, or a policy being trained",
    "    obs, reward, done, info = env.step(action)",
    "",
    "# info carries which checks settled this step, so credit is attributable",
  ].join("\n");
