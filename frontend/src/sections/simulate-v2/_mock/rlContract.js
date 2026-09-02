/**
 * The RL environment contract — the keystone.
 *
 * Everything upstream (reading the agent) and everything downstream (running,
 * scoring, training) meets here. The contract is what makes an environment an
 * *environment* rather than a test script: an adapter that fixes the modality,
 * an observation and action space, transition dynamics, a reward spec, and an
 * episode contract that says when a run is over.
 *
 * Two properties worth preserving:
 *
 *   Generic core, adapter-filled.  Observation and action are the same five
 *   fields in every modality. The adapter says what fills them. That is why a
 *   voice environment and a coding environment can share one runner.
 *
 *   Reward is not invented here.  The verifiers ARE the evals that grade a
 *   run, given weights. If reward had its own definition you could train an
 *   agent that scores well and still fails the release gate.
 */
import { MODALITY_FOR } from "./fidelity";
import { getAgentType } from "./agentTypes";

/* ── 1. modality adapter ─────────────────────────────────────────────────── */

export const ADAPTERS = [
  {
    id: "chat", label: "Chat", icon: "solar:chat-round-line-linear", color: "#2563EB",
    blurb: "Turn-taking text. The counterpart types.",
    observation: { transcript: "list[turn]", ui_state: "null" },
    action: ["say", "call_tool", "finish"],
  },
  {
    id: "voice", label: "Voice", icon: "solar:phone-calling-linear", color: "#7857FC",
    blurb: "Duplex audio. Interruptions and dead air are part of the state.",
    observation: { transcript: "list[turn]", audio_state: "{noise, barge_in, latency_ms}" },
    action: ["say", "call_tool", "finish"],
  },
  {
    id: "cua", label: "Computer use", icon: "solar:monitor-linear", color: "#0D9488",
    blurb: "A screen the agent looks at and clicks.",
    observation: { screenshot: "image", dom: "tree" },
    action: ["click", "type", "scroll", "call_tool", "finish"],
  },
  {
    id: "coding", label: "Coding", icon: "solar:code-square-linear", color: "#EA580C",
    blurb: "A repo, a test suite and a shell.",
    observation: { repo_diff: "patch", test_report: "junit" },
    action: ["edit_file", "run_tests", "call_tool", "finish"],
  },
  {
    id: "physical", label: "Physical", icon: "solar:cpu-bolt-linear", color: "#DC2626",
    blurb: "Robotics and worldsims. Continuous control, real clock.",
    observation: { sensors: "dict[str, float]", frame: "image", pose: "se3" },
    action: ["actuate", "grasp", "move_to", "finish"],
    future: true,
  },
  {
    id: "custom", label: "Custom", icon: "solar:widget-4-linear", color: "#CA8A04",
    blurb: "Bring your own spaces. The core five fields still apply.",
    observation: { payload: "your schema" },
    action: ["your verbs"],
    custom: true,
  },
];

/**
 * The modality actually in force.
 *
 * Not a setting. The modality is a consequence of what the agent *is*: you
 * chose the agent type when you connected, and the observation space, the
 * action space, the fidelity controls and the runtime connection all fall out
 * of it. A free-standing override on the contract page re-opened that settled
 * question and — worse — only reached two of those four, so a coding adapter
 * could sit on a phone line while the scenarios stayed voice.
 *
 * So there is one resolution point, and it reads the connected agent. An agent
 * that fits the environment's surface leaves the modality where it was; an
 * agent from a different surface moves it, and everything derived moves with
 * it. Correcting a misread means reconnecting on the Agent page.
 */
export const effectiveModality = (env, envState) => {
  const type = getAgentType(envState?.agent?.typeId);
  if (type && !type.surfaces.includes(env?.surface)) {
    return MODALITY_FOR[type.surfaces[0]] || "chat";
  }
  return MODALITY_FOR[env?.surface] || "chat";
};

/** The adapter in force, resolved the same single way. */
export const adapterOf = (env, envState) =>
  ADAPTERS.find((a) => a.id === effectiveModality(env, envState)) || ADAPTERS[0];

/**
 * True when the connected agent moved the modality off the environment's own
 * surface — the one case where the contract is not what the template promised.
 */
export const modalityMovedBy = (env, envState) => {
  const type = getAgentType(envState?.agent?.typeId);
  if (!type || type.surfaces.includes(env?.surface)) return null;
  return type;
};

/** The environment as its settings currently stand, for anything deriving from them. */
export const effectiveEnv = (env, envState) => ({
  ...env,
  difficulty: envState?.difficulty || env?.difficulty,
});

export const adapterFor = (env) =>
  ADAPTERS.find((a) => a.id === (MODALITY_FOR[env?.surface] || "chat")) || ADAPTERS[0];

export const getAdapter = (id) => ADAPTERS.find((a) => a.id === id);

/* ── 2. observation + action ─────────────────────────────────────────────── */

/**
 * The generic core is fixed. The adapter fills it. Showing both columns is the
 * point — it is what makes "one runner, six modalities" a claim you can check
 * rather than a slogan.
 */
export const observationSpace = (env, adapter) => {
  const a = adapter || adapterFor(env);
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
  const a = adapter || adapterFor(env);
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

/* ── 3. transition dynamics ──────────────────────────────────────────────── */

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
    (() => {
      /*
        Integrations show as one of two states:
          · If twins are attached, this is a real stateful sandbox and
            the row reflects that — scenarios can test the end state,
            not just the decision.
          · If no twins are attached, we fall back to shallow replay
            (the historical behaviour) and surface an "upgrade" hint
            that points to the Twins tab.
      */
      const twins = envState?.twins || [];
      if (twins.length > 0) {
        return {
          id: "integrations",
          label: "Twinned services",
          icon: "solar:server-square-linear",
          value: `${twins.length} twin${twins.length === 1 ? "" : "s"}`,
          note: "Third-party services the agent talks to are running as stateful twins seeded per-scenario. Runs test what actually landed — a message in Slack, a row in Salesforce — not just the decision to call.",
          to: "twins",
        };
      }
      return {
        id: "integrations",
        label: "Mocked integrations",
        icon: "solar:plug-circle-linear",
        value: `${stubbed.length || 1} stubbed`,
        note: `${stubbed.length ? stubbed.map((t) => t.name).join(", ") : "One write tool"} reaches outside the sandbox, so it replays a recorded response instead of firing. Scenarios ending there test the decision, not the delivery. Attach twins to test what actually landed.`,
        to: "twins",
      };
    })(),
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

/* ── 4. reward spec ──────────────────────────────────────────────────────── */

export const VERIFIER_KINDS = [
  { id: "goal", label: "Goal", color: "#16A34A", blurb: "Did the task get done. Terminal, sparse." },
  { id: "rubric", label: "Rubric", color: "#2563EB", blurb: "How well, judged per turn. Dense." },
  { id: "constraint", label: "Constraints", color: "#DC2626", blurb: "What must never happen. Penalty." },
];

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

/* ── 5. episode contract ─────────────────────────────────────────────────── */

/**
 * Terminate and truncate are different endings and conflating them corrupts
 * training: a truncated episode has no terminal value and must be bootstrapped,
 * while a terminated one does. Stating both here is what lets the RL interface
 * return an honest `done`.
 */
export const episodeContract = (env, envState) => {
  const adapter = adapterOf(env, envState);
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

/* ── completeness ────────────────────────────────────────────────────────── */

export const contractParts = (env, envState) => [
  { id: "adapter", label: "Modality adapter", done: true },
  { id: "spaces", label: "Observation + action", done: true },
  { id: "dynamics", label: "Transition dynamics", done: (envState?.personas || []).length > 0 || true },
  { id: "reward", label: "Reward spec", done: (envState?.evals || []).length > 0 },
  { id: "episode", label: "Episode contract", done: true },
];
