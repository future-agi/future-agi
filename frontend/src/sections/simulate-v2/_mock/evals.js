/**
 * Eval catalog for the simulation flow.
 *
 * `appliesTo` narrows the picker to evals that make sense for the environment
 * surface — an audio-quality eval on a terminal sandbox is noise.
 */

export const EVAL_CATALOG = [
  {
    id: "task_success",
    name: "Task success",
    category: "Outcome",
    blurb: "Did the agent actually complete the task the scenario asked for?",
    type: "LLM judge",
    appliesTo: "all",
    icon: "solar:target-linear",
    color: "#16A34A",
    threshold: 0.8,
    required: true,
  },
  {
    id: "policy_adherence",
    name: "Policy adherence",
    category: "Safety",
    blurb: "Checks the agent respected every business rule on the environment.",
    type: "Rule + LLM judge",
    appliesTo: "all",
    icon: "solar:shield-check-linear",
    color: "#7857FC",
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
    color: "#6D28D9",
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
    color: "#DC2626",
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
    color: "#EA580C",
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
    color: "#0891B2",
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
    color: "#EA580C",
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
    color: "#CA8A04",
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
    color: "#2563EB",
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
    color: "#DB2777",
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
    color: "#F43F5E",
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
    color: "#0D9488",
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
    color: "#7857FC",
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
    color: "#CA8A04",
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
    color: "#16A34A",
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
    color: "#525252",
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
    color: "#2563EB",
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
    color: "#8B5CF6",
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
    color: "#DC2626",
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
    color: "#C026D3",
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
    color: "#0D9488",
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
    color: "#DC2626",
    threshold: 1.0,
  },
];

export const EVAL_CATEGORIES = ["Outcome", "Safety", "Behaviour", "Quality", "Performance"];

export const getEval = (id) => EVAL_CATALOG.find((e) => e.id === id);

export const evalsForSurface = (surfaceId) =>
  EVAL_CATALOG.filter(
    (e) => e.appliesTo === "all" || e.appliesTo.includes(surfaceId),
  );

/**
 * The columns a simulation run produces, offered to the eval picker for
 * variable mapping. These are what exists per task once a run finishes, so an
 * eval's inputs can be mapped onto them the same way they map onto dataset
 * columns elsewhere in the product.
 */
export const SIMULATION_COLUMNS = [
  { field: "task", headerName: "Task", dataType: "text" },
  { field: "expected_outcome", headerName: "Expected outcome", dataType: "text" },
  { field: "persona", headerName: "Persona", dataType: "text" },
  { field: "transcript", headerName: "Transcript", dataType: "text" },
  { field: "agent_response", headerName: "Agent response", dataType: "text" },
  { field: "tool_calls", headerName: "Tool calls", dataType: "text" },
  { field: "business_rules", headerName: "Business rules", dataType: "text" },
  { field: "scenario_pack", headerName: "Scenario pack", dataType: "text" },
];

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
    color: "#7857FC",
    threshold: 0.8,
    category: "Custom",
    type: "LLM judge",
    ...known,
    ...applied,
  };
};


/**
 * Preview data for the eval picker's create-simulate source mode.
 *
 * That mode is built for exactly our situation — evals bound before the
 * simulation has run — so it renders the scenario chips and the
 * columns/value table with runtime fields marked as resolved later. It reads
 * one nested shape, so the environment, its agent and the chosen scenarios
 * are flattened into it here.
 */
export const simulationPreviewData = (env, envState, agentType) => {
  const scenarios = envState?.scenarios || [];
  const first = scenarios[0];

  return {
    // Voice/chat environments produce call transcripts; everything else is
    // stepped text, and the mode swaps its runtime vocabulary on this.
    sim_call_type: env?.surface === "voice" ? "voice" : "text",
    simulation_name: env?.name,
    simulation_type: "environment",
    agent_definition: envState?.agent
      ? {
          agent_name: agentType?.label,
          agent_type: agentType?.id,
          description: agentType?.blurb,
        }
      : undefined,
    simulator_agent: first?.persona
      ? {
          name: first.persona.name,
          description: [first.persona.role, ...(first.persona.traits || [])]
            .filter(Boolean)
            .join(" · "),
        }
      : undefined,
    scenario_info: first
      ? {
          name: first.title,
          description: first.task,
          scenario_type: first.critical ? "critical" : "standard",
          source: first.origin || "Scenario pack",
        }
      : undefined,
    // Display path is scenario.columns.<name>; the key is the id the mapping
    // persists, which for this prototype is the field name itself.
    scenario_columns: Object.fromEntries(
      SIMULATION_COLUMNS.map((c) => [c.field, { name: c.field, type: "string" }]),
    ),
    scenario_summaries: scenarios.map((sc, i) => ({
      id: sc.id || `scenario-${i}`,
      name: sc.title,
      scenario_type: sc.critical ? "critical" : "standard",
      persona: sc.persona ? { name: sc.persona.name } : undefined,
    })),
  };
};
