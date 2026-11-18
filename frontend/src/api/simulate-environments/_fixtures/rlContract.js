/**
 * The RL environment contract — the keystone.
 *
 * Everything upstream (reading the agent) and everything downstream (running,
 * scoring, training) meets here. The contract is what makes an environment an
 * *environment* rather than a test script: an adapter that fixes the modality,
 * an observation and action space, transition dynamics, a reward spec, and an
 * episode contract that says when a run is over.
 *
 *   Generic core, adapter-filled.  Observation and action are the same fields in
 *   every modality. The adapter says what fills them — that is why a voice
 *   environment and a coding environment can share one runner.
 *
 *   Reward is not invented here.  The verifiers ARE the evals that grade a run,
 *   given weights.
 *
 * Ported from the prototype's RL-contract mock. Deviations from the port:
 *  - `MODALITY_FOR` is inlined (the prototype imported it from its fidelity mock).
 *  - `adapterOf` resolves the adapter purely from the environment surface. The
 *    prototype also let a connected agent from a different surface move the
 *    modality, which needed its agent-types registry (`surfaces` per type); that
 *    registry is not ported in Phase-3, so the override is dropped and
 *    `adapterOf`/`episodeContract` take only `env`.
 *  - The twin branch of `transitionDynamics` (the `twinBacking` stateful-twin
 *    row) is stripped; the mocked-integrations fallback is the only path.
 *  - Colours are read from BUILD_TONES.
 */
import { BUILD_TONES } from "src/sections/simulate/environments/buildEnvironment/buildTones";

// The modality a surface resolves to. The observation space, action space,
// fidelity controls and runtime connection all fall out of it.
const MODALITY_FOR = {
  voice: "voice",
  chat: "chat",
  messaging: "chat",
  email: "chat",
  browser: "cua",
  computer: "cua",
  cli: "coding",
  api: "coding",
  mcp: "coding",
  sim: "cua",
  multi: "voice",
};

export const ADAPTERS = [
  {
    id: "chat", label: "Chat", icon: "solar:chat-round-line-linear", color: BUILD_TONES.blue,
    blurb: "Turn-taking text. The counterpart types.",
    observation: { transcript: "list[turn]", ui_state: "null" },
    action: ["say", "call_tool", "finish"],
  },
  {
    id: "voice", label: "Voice", icon: "solar:phone-calling-linear", color: BUILD_TONES.accent,
    blurb: "Duplex audio. Interruptions and dead air are part of the state.",
    observation: { transcript: "list[turn]", audio_state: "{noise, barge_in, latency_ms}" },
    action: ["say", "call_tool", "finish"],
  },
  {
    id: "cua", label: "Computer use", icon: "solar:monitor-linear", color: BUILD_TONES.tealDeep,
    blurb: "A screen the agent looks at and clicks.",
    observation: { screenshot: "image", dom: "tree" },
    action: ["click", "type", "scroll", "call_tool", "finish"],
  },
  {
    id: "coding", label: "Coding", icon: "solar:code-square-linear", color: BUILD_TONES.orange,
    blurb: "A repo, a test suite and a shell.",
    observation: { repo_diff: "patch", test_report: "junit" },
    action: ["edit_file", "run_tests", "call_tool", "finish"],
  },
  {
    id: "physical", label: "Physical", icon: "solar:cpu-bolt-linear", color: BUILD_TONES.red,
    blurb: "Robotics and worldsims. Continuous control, real clock.",
    observation: { sensors: "dict[str, float]", frame: "image", pose: "se3" },
    action: ["actuate", "grasp", "move_to", "finish"],
    future: true,
  },
  {
    id: "custom", label: "Custom", icon: "solar:widget-4-linear", color: BUILD_TONES.amber,
    blurb: "Bring your own spaces. The core five fields still apply.",
    observation: { payload: "your schema" },
    action: ["your verbs"],
    custom: true,
  },
];

/** The adapter in force, resolved from the environment surface. */
export const adapterOf = (env) =>
  ADAPTERS.find((a) => a.id === (MODALITY_FOR[env?.surface] || "chat")) || ADAPTERS[0];

/**
 * The generic core is fixed. The adapter fills it. Showing both columns is the
 * point — it is what makes "one runner, six modalities" a claim you can check
 * rather than a slogan.
 */
export const observationSpace = (env, adapter) => {
  const a = adapter || adapterOf(env);
  return [
    { field: "goal", type: "str", filled: "The scenario's task, as the persona states it.", generic: true },
    { field: "world", type: "dict", filled: `${env?.seed?.tables?.length || 0} seeded tables, reset each episode.`, generic: true },
    { field: "history", type: "list[step]", filled: "Every action and result so far this episode.", generic: true },
    { field: "sub_goals", type: "list[check]", filled: "The checks not yet settled.", generic: true },
    ...Object.entries(a.observation).map(([field, type]) => ({
      field, type, filled: `Filled by the ${a.label.toLowerCase()} adapter.`, generic: false,
    })),
  ];
};

export const actionSpace = (env, adapter) => {
  const a = adapter || adapterOf(env);
  const tools = env?.tools || [];
  return a.action.map((verb) => ({
    verb,
    args:
      verb === "call_tool" ? `name: enum[${tools.length} tools], args: dict`
        : verb === "say" ? "text: str"
          : verb === "finish" ? "summary: str"
            : "adapter-defined",
    note:
      verb === "call_tool" ? "The tools on the contract, with their real argument names."
        : verb === "finish" ? "Ends the episode. Terminal — see the episode contract."
          : `Provided by the ${a.label.toLowerCase()} adapter.`,
  }));
};

/**
 * What moves the world between steps, and it is never only the agent: the
 * personas act, the mocked integrations answer, and the perturbations happen
 * whether anyone asked for them or not.
 */
export const transitionDynamics = (env, envState) => {
  const tools = env?.tools || [];
  const stubbed = tools.filter((t) => /refund|issue|charge|send|delete|book/i.test(t.name));
  return [
    {
      id: "actors",
      label: "Actors",
      icon: "solar:users-group-two-rounded-linear",
      value: `${(envState?.actors || []).length || 3} in this environment`,
      note: "Other parties with goals of their own, pulling against the task — a colleague arguing for something else, a supervisor with their own criteria. They are dynamics, not task: the persona states the goal, the actor pulls against it.",
      to: "actors",
    },
    {
      id: "integrations",
      label: "Mocked integrations",
      icon: "solar:plug-circle-linear",
      value: `${stubbed.length || 1} stubbed`,
      note: `${stubbed.length ? stubbed.map((t) => t.name).join(", ") : "One write tool"} reaches outside the sandbox, so it replays a recorded response instead of firing. Scenarios ending there test the decision, not the delivery. Attach twins to test what actually landed.`,
      to: "twins",
    },
    {
      id: "perturbations",
      label: "Perturbations",
      icon: "solar:tuning-2-linear",
      value: "per modality",
      note: "Noise, barge-in, typos, latency, flaky downstreams. Applied by the adapter so the same scenario is a different problem at a different fidelity.",
      to: "fidelity",
    },
  ];
};

export const rewardSpec = (env) => {
  const rules = env?.rules || [];
  return {
    goal: [
      { name: "task_completed", weight: 1.0, note: "The scenario's stated outcome was reached." },
      { name: "under_reference_length", weight: 0.2, note: "Done in no more steps than the reference solution." },
    ],
    rubric: [
      { name: "sub_goal_settled", weight: 0.15, note: "Each check that settles pays out when it settles." },
      { name: "grounded", weight: 0.1, note: "Claims trace to a tool result or seeded row." },
    ],
    constraint: [
      ...rules.slice(0, 3).map((r) => ({ name: "hard_rule_violation", weight: -1.0, note: r })),
      { name: "step_cost", weight: -0.01, note: "Every step, so dithering is not free." },
    ],
  };
};

/**
 * Terminate and truncate are different endings and conflating them corrupts
 * training: a truncated episode has no terminal value and must be bootstrapped,
 * while a terminated one does. Stating both here is what lets the RL interface
 * return an honest `done`.
 */
export const episodeContract = (env) => {
  const adapter = adapterOf(env);
  return {
    terminate: [
      { when: "agent calls finish()", note: "Terminal. Goal verifiers settle and the episode has a value." },
      { when: "a hard rule is violated", note: "Terminal and failed. The run stops rather than accumulating more reward." },
      { when: "the persona hangs up or leaves", note: "Terminal. Whatever settled, settled." },
    ],
    truncate: [
      { when: `${adapter.id === "coding" ? "60" : "40"} steps`, note: "Not a failure. No terminal value — bootstrap from the last state." },
      { when: adapter.id === "voice" ? "8 minutes wall clock" : "12 minutes wall clock", note: "The sandbox is reclaimed. Also not a failure." },
    ],
    clock: {
      mode: adapter.id === "voice" || adapter.id === "physical" ? "real-time" : "stepped",
      note:
        adapter.id === "voice" || adapter.id === "physical"
          ? "Real time: latency and dead air are part of the problem, so the clock cannot be faked."
          : "Stepped: the world only advances when the agent acts, so a slow model is not a worse agent.",
    },
    seed: {
      note: "Every episode is seeded. The same seed replays the same world, the same persona turns and the same mocked responses — which is what makes a failing scenario debuggable.",
    },
  };
};

export const contractParts = (env, envState) => [
  { id: "adapter", label: "Modality adapter", done: true },
  { id: "spaces", label: "Observation + action", done: true },
  { id: "dynamics", label: "Transition dynamics", done: true },
  { id: "reward", label: "Reward spec", done: (envState?.evals || []).length > 0 },
  { id: "episode", label: "Episode contract", done: true },
];
