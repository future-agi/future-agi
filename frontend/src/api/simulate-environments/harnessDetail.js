import { environmentName } from "src/pages/dashboard/harness/harnessShared";
import {
  stageToStatus,
  buildStatusFor,
} from "src/sections/simulate/environments/helpers/harnessJobToRow";
import { BUILD_STATUS } from "src/sections/simulate/environments/myEnvironments.constants";

/**
 * §6 environment-detail adapter.
 *
 * Maps the `GET /harness-environments/{id}/` body onto the same `env` / `world`
 * / `envState` shapes the workspace already reads, so the existing panels light
 * up with real data. It is real-first: every section is null until the stage
 * that produces it finishes (§6/§7), so callers overlay fixtures/MOCK_WORLD for
 * whatever is still null. New structured fields the contract added — amendments
 * ({subject,note}[]), the sub-goal catalogue, end_conditions, world.stores,
 * personas — are attached to `env` verbatim so panels can prefer them over the
 * fixture derivations.
 *
 * Pure and side-effect free: unit-tested against the contract's example JSON.
 */

const AGENT_TYPE = { voice: "voice", chat: "text" };

// contract.tools[] → the { name, args, desc } shape stageOutputsToWorld emits.
const toWorldTool = (tool) =>
  typeof tool === "string"
    ? { name: tool, args: [], desc: tool }
    : {
        name: tool?.name,
        args: tool?.args || [],
        desc: tool?.description || tool?.name || "",
        argTypes: tool?.arg_types || undefined,
        requires: tool?.requires || undefined,
      };

// world.stores[] → the seed.tables[] shape the SeededDataCard / schema surfaces
// read: one row per table with { name, rows, note }. The note carries the
// store's capability + engine so the provenance is legible.
const storeTables = (stores = []) =>
  (Array.isArray(stores) ? stores : []).flatMap((store) =>
    (Array.isArray(store?.tables) ? store.tables : []).map((table) => ({
      name: table?.name,
      rows: table?.rows,
      note: [store?.capability, store?.engine].filter(Boolean).join(" · "),
    })),
  );

// §6 scenario → the flat scenario-pool row the ScenarioTable/Detail read. Real
// per-scenario sub_goals become `subTasks` (the row's real-first source that
// ScenarioDetail already prefers over the fixture), and use_case is copied
// verbatim so Flows grouping matches `real_use_cases` by exact string.
const toScenarioRow = (row) => ({
  id: row?.scenario_key || row?.scenario_id || row?.name,
  name: row?.name,
  title: row?.name,
  task: row?.instruction,
  situation: row?.situation || row?.instruction,
  useCase: row?.use_case || "Generated test case",
  expected: row?.outcome || row?.tests || "",
  persona: row?.persona || null,
  status: row?.status,
  callExecutionId: row?.call_execution_id || null,
  subTasks: (Array.isArray(row?.sub_goals) ? row.sub_goals : []).map((sg) => ({
    id: sg?.name,
    label: sg?.what || sg?.name,
    kind: sg?.kind,
  })),
});

// evaluations.selected[] → the applied-eval rows useAppliedEvals/EvalRow read.
const toEvalRow = (evalRow) => ({
  id: evalRow?.id,
  name: evalRow?.name,
  blurb: evalRow?.description || "",
  runnable: evalRow?.runnable ?? true,
});

export function harnessDetailToEnvironment(detail) {
  if (!detail) return null;
  const overview = detail.overview || {};
  const contract = detail.contract || null;
  const world = detail.world || null;
  const settings = detail.settings || {};

  const tools = contract?.tools ? contract.tools.map(toWorldTool).filter((t) => t.name) : null;
  const rules = contract?.hard_constraints
    ? contract.hard_constraints.map(String).filter(Boolean)
    : null;
  const description = contract?.one_liner || overview.description || null;
  const services = world?.runtime?.services || null;
  const seedTables = world?.stores ? storeTables(world.stores) : null;

  // Only set world keys we actually have, so the caller can overlay fixtures for
  // the rest (same contract as stageOutputsToWorld's partial world).
  const derivedWorld = {};
  if (tools?.length) derivedWorld.tools = tools;
  if (rules?.length) derivedWorld.rules = rules;
  if (description) derivedWorld.description = description;
  if (seedTables?.length || services?.length) {
    derivedWorld.seed = { tables: seedTables || [], services: services || [] };
  }

  const env = {
    id: detail.id || overview.id,
    name: overview.name || environmentName({ job_id: detail.id }),
    description,
    domain: overview.domain ?? null,
    agentType: AGENT_TYPE[overview.agent_type] || overview.agent_type || null,
    surface: contract?.modality || overview.agent_type || undefined,
    status: overview.status ? stageToStatus(overview.status) : undefined,
    buildStatus: overview.status ? buildStatusFor(overview.status) : BUILD_STATUS.BUILDING,
    tools: derivedWorld.tools,
    rules: derivedWorld.rules,
    seed: derivedWorld.seed,
    evalPreset: contract?.chosen_evals || undefined,
    testSubject: overview.agent ?? null,
    platform: {
      runTestId: overview.run?.run_test_id,
      testExecutionId: overview.run?.test_execution_id,
      simulationUrl: overview.run?.simulation_url,
    },
    // New structured contract fields, attached verbatim so panels can prefer
    // them over the fixture derivations (fixture fallback stays for nulls).
    amendments: contract?.amendments || null,
    subGoals: contract?.sub_goals || null,
    endConditions: contract?.end_conditions || null,
    useCases: contract?.real_use_cases || null,
    personas: world?.personas || null,
    stores: world?.stores || null,
    callDirection: contract?.call_direction ?? null,
    // Real per-table field types and per-tool callables, when ALK read them
    // from source. Empty for provider agents, which have neither.
    dataSchema: contract?.data_schema || null,
    toolEntrypoints: contract?.tool_entrypoints || null,
    systemPromptExcerpt: contract?.system_prompt_excerpt || null,
    counts: {
      flows: overview.flows_count ?? null,
      guardrails: overview.guardrails_count ?? null,
      personas: overview.personas_count ?? null,
      subGoals: overview.sub_goals_count ?? null,
      scenarios: overview.scenario_count ?? null,
      tools: overview.tools_count ?? null,
      runs: overview.runs_count ?? null,
      evaluations: overview.evaluations_count ?? null,
    },
    // Provenance by actual presence: a field the §6 payload carried is "real";
    // one still null (built later, or an older job) stays "mock" so the caller's
    // fixture/MOCK_WORLD fallback is badged honestly.
    provenance: {
      tools: tools?.length ? "real" : "mock",
      rules: rules?.length ? "real" : "mock",
      description: description ? "real" : "mock",
      seedTables: seedTables?.length ? "real" : "mock",
      seedServices: services?.length ? "real" : "mock",
      scenarios: detail.scenarios?.length ? "real" : "mock",
    },
    detailReady: contract != null,
  };

  const scenarios = Array.isArray(detail.scenarios)
    ? detail.scenarios.map(toScenarioRow)
    : null;
  const selectedEvals = Array.isArray(detail.evaluations?.selected)
    ? detail.evaluations.selected.map(toEvalRow)
    : null;

  const agentConnector =
    settings.agent?.connector || overview.agent_type || "auto";
  const version = {
    label: "v1",
    note: "First run of this environment.",
    createdAt: overview.last_updated || overview.created_at,
  };

  const envState = {
    agent: {
      typeId: agentConnector,
      via: "endpoint",
      values: {},
      connectedAt: overview.last_updated || overview.created_at,
    },
    scenarios: scenarios || undefined,
    scenarioSource: "harness",
    evals: selectedEvals || [],
    runs: [],
    agentVersions: [version],
    envVersions: [version],
    activeAgentVersion: "v1",
    activeEnvVersion: "v1",
  };

  return { env, world: derivedWorld, envState, overview, contract, detail };
}
