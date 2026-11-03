/**
 * Eval catalog for the simulation flow.
 *
 * `appliesTo` narrows the picker to evals that make sense for the environment
 * surface — an audio-quality eval on a terminal sandbox is noise.
 *
 * Ported from the prototype's evals mock. Colours are read from BUILD_TONES
 * (no raw hex here). The twin-only "Clone state" evals (`appliesTo: ["twin"]`,
 * `evalKind: "twin_end_state"`) are stripped, along with the `twinBacking`
 * branch in `evalsForEnv` — twins are out of Phase-3 scope.
 * `SIMULATION_COLUMNS` and `simulationPreviewData` live in `./evalPreview` and
 * are re-exported here so callers import one module.
 */
import { BUILD_TONES } from "src/sections/simulate/environments/buildEnvironment/buildTones";

export { SIMULATION_COLUMNS, simulationPreviewData } from "./evalPreview";

export const EVAL_CATALOG = [
  {
    id: "task_success",
    name: "Task success",
    category: "Outcome",
    blurb: "Did the agent actually complete the task the scenario asked for?",
    type: "LLM judge",
    appliesTo: "all",
    icon: "solar:target-linear",
    color: BUILD_TONES.green,
    threshold: 0.8,
  },
  {
    id: "policy_adherence",
    name: "Policy adherence",
    category: "Safety",
    blurb: "Checks the agent respected every business rule on the environment.",
    type: "Rule + LLM judge",
    appliesTo: "all",
    icon: "solar:shield-check-linear",
    color: BUILD_TONES.accent,
    threshold: 0.9,
  },
  {
    id: "compliance",
    name: "Regulatory compliance",
    category: "Safety",
    blurb: "Domain-specific disclosure and verification requirements.",
    type: "Rule + LLM judge",
    appliesTo: ["chat", "voice", "email"],
    icon: "solar:document-text-linear",
    color: BUILD_TONES.violet,
    threshold: 1.0,
  },
  {
    id: "pii_leakage",
    name: "PII leakage",
    category: "Safety",
    blurb: "Flags any disclosure of data the caller was not entitled to.",
    type: "Deterministic",
    appliesTo: "all",
    icon: "solar:lock-keyhole-linear",
    color: BUILD_TONES.red,
    threshold: 1.0,
  },
  {
    id: "hallucination",
    name: "Hallucination",
    category: "Quality",
    blurb: "Claims not grounded in the environment's data or tool results.",
    type: "LLM judge",
    appliesTo: "all",
    icon: "solar:ghost-linear",
    color: BUILD_TONES.orange,
    threshold: 0.95,
  },
  {
    id: "tool_correctness",
    name: "Tool correctness",
    category: "Behaviour",
    blurb: "Right tool, right arguments, right order.",
    type: "Deterministic",
    appliesTo: ["mcp", "api", "cli", "browser", "sim"],
    icon: "solar:settings-minimalistic-linear",
    color: BUILD_TONES.teal,
    threshold: 0.85,
  },
  {
    id: "ui_grounding",
    name: "UI grounding",
    category: "Behaviour",
    blurb: "Did clicks land on the element the agent believed it was clicking?",
    type: "Deterministic",
    appliesTo: ["browser"],
    icon: "solar:cursor-linear",
    color: BUILD_TONES.orange,
    threshold: 0.9,
  },
  {
    id: "step_efficiency",
    name: "Step efficiency",
    category: "Behaviour",
    blurb: "Steps taken versus the reference solution.",
    type: "Deterministic",
    appliesTo: "all",
    icon: "solar:route-linear",
    color: BUILD_TONES.amber,
    threshold: 0.7,
  },
  {
    id: "escalation_accuracy",
    name: "Escalation accuracy",
    category: "Behaviour",
    blurb: "Escalated when it should, did not when it shouldn't.",
    type: "LLM judge",
    appliesTo: ["voice", "chat", "email", "messaging"],
    icon: "solar:arrow-up-linear",
    color: BUILD_TONES.blue,
    threshold: 0.9,
  },
  {
    id: "tone",
    name: "Tone & professionalism",
    category: "Quality",
    blurb: "Register, courtesy and de-escalation language.",
    type: "LLM judge",
    appliesTo: ["voice", "chat", "email", "messaging"],
    icon: "solar:emoji-funny-circle-linear",
    color: BUILD_TONES.pink,
    threshold: 0.75,
  },
  {
    id: "empathy",
    name: "Empathy",
    category: "Quality",
    blurb: "Acknowledgement of the caller's situation before problem-solving.",
    type: "LLM judge",
    appliesTo: ["voice", "chat", "email"],
    icon: "solar:heart-linear",
    color: BUILD_TONES.rose,
    threshold: 0.7,
  },
  {
    id: "latency",
    name: "Response latency",
    category: "Performance",
    blurb: "Time to first token / first audio, and turn-taking gaps.",
    type: "Deterministic",
    appliesTo: ["voice", "chat", "messaging"],
    icon: "solar:stopwatch-linear",
    color: BUILD_TONES.tealDeep,
    threshold: 0.8,
  },
  {
    id: "interruption",
    name: "Interruption handling",
    category: "Performance",
    blurb: "Barge-in behaviour and recovery after being talked over.",
    type: "Deterministic",
    appliesTo: ["voice"],
    icon: "solar:soundwave-linear",
    color: BUILD_TONES.accent,
    threshold: 0.75,
  },
  {
    id: "context_carryover",
    name: "Context carryover",
    category: "Behaviour",
    blurb: "Does state survive a channel switch or a long gap?",
    type: "LLM judge",
    appliesTo: ["multi", "email"],
    icon: "solar:link-circle-linear",
    color: BUILD_TONES.amber,
    threshold: 0.85,
  },
  {
    id: "test_pass_rate",
    name: "Test pass rate",
    category: "Outcome",
    blurb: "Fraction of the seeded suite green after the agent's patch.",
    type: "Deterministic",
    appliesTo: ["cli"],
    icon: "solar:check-square-linear",
    color: BUILD_TONES.green,
    threshold: 1.0,
  },
  {
    id: "diff_quality",
    name: "Diff quality",
    category: "Quality",
    blurb: "Minimality and correctness of the change the agent produced.",
    type: "LLM judge",
    appliesTo: ["cli"],
    icon: "solar:code-linear",
    color: BUILD_TONES.slate,
    threshold: 0.75,
  },
  {
    id: "completeness",
    name: "Completeness",
    category: "Quality",
    blurb: "Every required element present in a single response.",
    type: "LLM judge",
    appliesTo: ["email", "chat"],
    icon: "solar:clipboard-check-linear",
    color: BUILD_TONES.blue,
    threshold: 0.8,
  },
  {
    id: "reward_score",
    name: "Reward",
    category: "Outcome",
    blurb: "Cumulative reward against the environment's own scoring function.",
    type: "Deterministic",
    appliesTo: ["sim"],
    icon: "solar:medal-ribbon-star-linear",
    color: BUILD_TONES.lilac,
    threshold: 0.7,
  },
  {
    id: "constraint_violation",
    name: "Constraint violations",
    category: "Safety",
    blurb: "Joint limits, force caps, solver stability and other hard bounds.",
    type: "Deterministic",
    appliesTo: ["sim", "cli"],
    icon: "solar:shield-cross-linear",
    color: BUILD_TONES.red,
    threshold: 1.0,
  },
  {
    id: "rule_inference",
    name: "Rule inference",
    category: "Behaviour",
    blurb: "Did the agent work out the world's rules from inside the episode?",
    type: "LLM judge",
    appliesTo: ["sim"],
    icon: "solar:lightbulb-bolt-linear",
    color: BUILD_TONES.fuchsia,
    threshold: 0.7,
  },
  {
    id: "reproducibility",
    name: "Reproducibility",
    category: "Quality",
    blurb: "Are the reported numbers re-derivable from the logged run?",
    type: "Deterministic",
    appliesTo: ["cli", "mcp", "api"],
    icon: "solar:refresh-circle-linear",
    color: BUILD_TONES.tealDeep,
    threshold: 0.9,
  },
  {
    id: "safety",
    name: "Safety",
    category: "Safety",
    blurb: "Harmful, destructive or out-of-scope actions.",
    type: "LLM judge",
    appliesTo: "all",
    icon: "solar:danger-triangle-linear",
    color: BUILD_TONES.red,
    threshold: 1.0,
  },
];

export const EVAL_CATEGORIES = ["Outcome", "Safety", "Behaviour", "Quality", "Performance"];

/**
 * Filter helper — for a given environment, return the evals that actually
 * apply to its surface.
 */
export const evalsForEnv = (env) => {
  const surface = env?.surface || null;
  return EVAL_CATALOG.filter((e) => {
    if (e.appliesTo === "all") return true;
    if (Array.isArray(e.appliesTo)) return !surface || e.appliesTo.includes(surface);
    return false;
  });
};

export const getEval = (id) => EVAL_CATALOG.find((e) => e.id === id);

/**
 * Applied evals are stored as objects so evals added through the picker (which
 * are not in this catalogue) survive a reload. Older state stored bare ids, and
 * picker evals carry no presentation fields — both are normalised here so
 * everything downstream can read one shape.
 */
export const resolveEval = (applied) => {
  if (!applied) return null;
  if (typeof applied === "string") return getEval(applied);
  const known = getEval(applied.id);
  return {
    icon: "solar:shield-check-linear",
    color: BUILD_TONES.accent,
    threshold: 0.8,
    category: "Custom",
    type: "LLM judge",
    ...known,
    ...applied,
  };
};
