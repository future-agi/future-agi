/**
 * The columns a simulation run produces, offered to the eval picker for
 * variable mapping, plus the preview payload its create-simulate source mode
 * reads. Split out of `evalCatalog.js` to keep that file under the size cap;
 * both symbols are re-exported from there so callers import one module.
 */

/**
 * The columns a simulation run produces. These are what exists per task once a
 * run finishes, so an eval's inputs can be mapped onto them the same way they
 * map onto dataset columns elsewhere in the product.
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
