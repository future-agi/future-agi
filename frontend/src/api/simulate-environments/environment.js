import { useMemo, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { getHarnessJob } from "src/api/harness/harness";
import { getHarnessEnvironment } from "src/api/simulate-environments/harnessEnvironments";
import { harnessDetailToEnvironment } from "src/api/simulate-environments/harnessDetail";
import {
  environmentName,
  terminalStages,
  stages,
  completedStageCount,
} from "src/pages/dashboard/harness/harnessShared";
import { useEnvironmentsStore } from "src/sections/simulate/environments/store/useEnvironmentsStore";
import {
  VOICE_CONNECTORS,
  stageToStatus,
  buildStatusFor,
} from "src/sections/simulate/environments/helpers/harnessJobToRow";
import { generatedPool } from "./_fixtures/scenarioPool";
import { MOCK_WORLD } from "./_fixtures/world";
import { usePrebuiltEnvironments } from "./prebuilt";
import { conversationInFlight } from "./conversationProjection";

// While the real world seam (stage_outputs) is still thin, an environment whose
// outputs carry nothing parseable falls back to the MOCK_WORLD overlay. Flip this
// to false (and delete _fixtures/world.js) once the backend fills every output.
export const MOCK_WORLD_OVERLAY = true;

// The §6 environment-detail endpoint is implemented on the backend branch but not
// yet merged/served (see environments-api-contracts §6). Until it lands and the
// Swagger surface is regenerated, GET /harness-environments/{id}/ 404s, so the
// detail query stays disabled to keep the live workspace error-free. Flip to true
// once the backend serves §6 — the whole real-detail path (harnessDetailToEnvironment
// merge + real scenarios/evals/amendments/end_conditions/stores) turns on with it.
export const HARNESS_DETAIL_ENABLED = false;

// The harness detail poll cadence, matching HarnessDetail's own 2s tick, with a
// faster 1s tick while a conversation turn is in flight so a reply lands promptly.
const REFETCH_MS = 2000;
const REFETCH_ACTIVE_MS = 1000;

// A READY env is terminal, so without the conversation check a chat reply would
// never poll in. Poll fast while the agent has a turn in flight; otherwise stop
// at a terminal stage; otherwise the steady live tick. `waiting_for_user` is not
// "in flight" — nothing changes server-side until the user answers, and we keep
// the composer enabled while waiting.
const jobRefetchInterval = (data) => {
  if (conversationInFlight(data?.conversation)) return REFETCH_ACTIVE_MS;
  return terminalStages.has(data?.status?.stage) ? false : REFETCH_MS;
};

// Shared react-query config for the single harness-job poll. The build page's
// progress hook and the workspace both read the same ["harness-job", id] cache
// entry, so there is one network poll that stops itself at a terminal stage.
// The app's axios interceptor rejects a customError carrying the HTTP status at
// `statusCode` (see utils/axios.js) — NOT `response.status`, which is undefined on
// a rejected request. Read `statusCode` first, tolerating a raw axios error too.
const errorStatus = (error) => error?.statusCode ?? error?.response?.status;

// A 404 (job gone / unknown id) is terminal: it must not be retried or polled,
// or a purged/unresolvable id floods the network with a request every 2s and the
// workspace never settles on its "not found" state.
const isJobNotFound = (error) => errorStatus(error) === 404;

export const harnessJobQuery = (envId, { enabled = true } = {}) => ({
  queryKey: ["harness-job", envId],
  queryFn: () => getHarnessJob(envId),
  enabled: Boolean(envId) && enabled,
  retry: (failureCount, error) => !isJobNotFound(error) && failureCount < 3,
  refetchInterval: (query) =>
    isJobNotFound(query.state.error) ? false : jobRefetchInterval(query.state.data),
});

// §6 environment detail. Fetched once on open and refetched by the workspace
// when the job poll crosses `validating_scenarios` (sections appear) or reaches
// a terminal stage (results appear) — the detail endpoint carries no live
// progress of its own (§7), so the ["harness-job", id] poll stays the heartbeat.
export const harnessEnvironmentKey = (envId) => ["harness-environment", envId];

export const harnessEnvironmentQuery = (envId, { enabled = true } = {}) => ({
  queryKey: harnessEnvironmentKey(envId),
  queryFn: () => getHarnessEnvironment(envId),
  enabled: Boolean(envId) && enabled,
  retry: (failureCount, error) => !isJobNotFound(error) && failureCount < 3,
});

// A voice transport in the detected connectors wins, same rule as the My
// Environments row mapping.
const agentTypeFor = (connectors = []) =>
  connectors.some((name) => VOICE_CONNECTORS.includes(name)) ? "voice" : "text";

// The real `environment.data.seed` is `manifest.seed` — an arbitrary shape, only
// occasionally the { name, rows, note } tables the sandbox card renders. Return
// tables only when the seed actually carries name-bearing rows, so the caller can
// mock-fill an empty result rather than blank the card.
function seedTablesFromEnvironment(environment = {}) {
  const seed = environment.seed;
  const rows = Array.isArray(seed)
    ? seed
    : Array.isArray(seed?.tables)
      ? seed.tables
      : [];
  return rows
    .map((table) => ({
      name: table?.name,
      rows: table?.rows ?? table?.row_count,
      note: table?.note || "",
    }))
    .filter((table) => table.name);
}

// Parse the run's stage_outputs (kinds contract / environment / scenarios — see
// HarnessDetail's StageOutput) into world fields. Returns `{ world, real }` where
// `world` is null when nothing is parseable (so the caller can overlay MOCK_WORLD)
// and `real` is the set of world keys that came from REAL outputs — the caller
// uses it to mark mock-filled fields. Only non-empty fields are set, so a partial
// contract still lets the overlay fill the gaps.
export function stageOutputsToWorld(stageOutputs = []) {
  const outputs = Array.isArray(stageOutputs) ? stageOutputs : [];
  const contract = outputs.find((o) => o?.kind === "contract")?.data || {};
  const environment = outputs.find((o) => o?.kind === "environment")?.data || {};
  const scenariosOut = outputs.find((o) => o?.kind === "scenarios")?.data;

  const tools = (contract.tools || [])
    .map((tool) =>
      typeof tool === "string"
        ? { name: tool, args: [], desc: tool }
        : {
            name: tool?.name,
            args: tool?.args || [],
            desc: tool?.description || tool?.name || "",
          },
    )
    .filter((tool) => tool.name);
  const rules = (contract.hard_constraints || []).map(String).filter(Boolean);
  const description = contract.one_liner || null;
  const services = environment.services || [];
  const seedTables = seedTablesFromEnvironment(environment);
  const scenarios = Array.isArray(scenariosOut) ? scenariosOut : [];

  const world = {};
  const real = new Set();
  if (tools.length) {
    world.tools = tools;
    real.add("tools");
  }
  if (rules.length) {
    world.rules = rules;
    real.add("rules");
  }
  if (description) {
    world.description = description;
    real.add("description");
  }
  if (services.length || seedTables.length) {
    world.seed = { tables: seedTables, services };
    if (seedTables.length) real.add("seedTables");
    if (services.length) real.add("seedServices");
  }
  if (scenarios.length) {
    world.scenarios = scenarios;
    real.add("scenarios");
  }

  return { world: Object.keys(world).length ? world : null, real };
}

// Raw scenario stage-output rows carry { name, instruction, use_case }; the
// scenario table reads the derived pool shape. Map them so the harness path
// renders the same table the client path does.
const scenarioFromOutput = (row) => ({
  id: row.scenario_key || row.name,
  name: row.name,
  title: row.name,
  task: row.instruction,
  situation: row.instruction,
  useCase: row.use_case || "Generated test case",
  expected: "",
  persona: null,
});

// The initial per-env state for an environment that only exists in the harness
// backend: an endpoint agent, scenarios from the run (or the derived pool sliced
// to the run's count) and v1 versions. Real runs arrive from the executions API.
export function harnessEnvState(item, world) {
  const job = item?.job || {};
  const status = item?.status || {};
  const resolved = world || MOCK_WORLD;
  const poolEnv = { ...resolved, id: job.job_id };

  // Prefer scenarios parsed from stage_outputs; then the job's own registered
  // scenarios (serialize_job top-level `scenarios[]`); then the derived pool.
  const scenarios =
    (resolved.scenarios?.length && resolved.scenarios.map(scenarioFromOutput)) ||
    (Array.isArray(item?.scenarios) && item.scenarios.length
      ? item.scenarios.map(scenarioFromOutput)
      : null) ||
    generatedPool(poolEnv).slice(0, job.scenario_count ?? undefined);

  const connector = item?.credentials?.detected_connectors?.[0] || "auto";
  const version = { label: "v1", note: "First run of this environment.", createdAt: status.updated_at };

  return {
    agent: {
      typeId: connector,
      via: "endpoint",
      values: {},
      connectedAt: status.updated_at,
    },
    scenarios,
    scenarioSource: "harness",
    evals: [],
    runs: [],
    agentVersions: [version],
    envVersions: [version],
    activeAgentVersion: "v1",
    activeEnvVersion: "v1",
  };
}

// Map a harness job detail into the environment record the workspace reads. The
// world fields overlay MOCK_WORLD when the run's outputs carry nothing parseable.
export function harnessJobToEnvironment(item) {
  const job = item?.job || {};
  const status = item?.status || {};
  const { world: parsed, real } = stageOutputsToWorld(item?.stage_outputs);

  // Real-first, mock-fill. The base overlay fills whole missing fields from
  // MOCK_WORLD; `seed` is then merged field-aware so a real `seed.services` does
  // not spread-wipe the mock-filled `seed.tables` (or vice-versa).
  const world = MOCK_WORLD_OVERLAY
    ? { ...MOCK_WORLD, ...(parsed ?? {}) }
    : { ...(parsed ?? {}) };
  if (MOCK_WORLD_OVERLAY) {
    world.seed = {
      tables: real.has("seedTables")
        ? parsed.seed.tables
        : MOCK_WORLD.seed?.tables || [],
      services: real.has("seedServices")
        ? parsed.seed.services
        : parsed?.seed?.services || MOCK_WORLD.seed?.services || [],
    };
  }

  // Per-field provenance so the workspace can badge mock-filled sections. A field
  // is "mock" when its value came from the MOCK_WORLD overlay rather than a real
  // stage output.
  const mark = (key) => (real.has(key) ? "real" : "mock");
  const provenance = {
    tools: mark("tools"),
    rules: mark("rules"),
    description: mark("description"),
    seedTables: mark("seedTables"),
    seedServices: mark("seedServices"),
    scenarios: mark("scenarios"),
  };

  const env = {
    id: job.job_id,
    name: environmentName(job),
    agentType: agentTypeFor(item?.credentials?.detected_connectors),
    status: stageToStatus(status.stage),
    buildStatus: buildStatusFor(status.stage),
    // The "whose fault" line for a failed build ({domain, stage, code, message},
    // see §7). Null unless the job stage is terminal-failed.
    buildError: status.failure || null,
    buildProgress: {
      done: completedStageCount(status, item?.events),
      total: stages.length,
    },
    platform: {
      runTestId: item?.platform?.run_test_id,
      testExecutionId: item?.platform?.test_execution_id,
    },
    stageOutputs: item?.stage_outputs,
    tools: world.tools,
    rules: world.rules,
    seed: world.seed,
    description: world.description,
    evalPreset: world.evalPreset,
    provenance,
  };

  return { env, world };
}

// Header Run-simulation gating: a built harness job can run through the product
// bridge as soon as it has a run-test id, even before the client canRun is met.
export const canRunHeader = (source, env, canRun) =>
  source === "harness" ? Boolean(env?.platform?.runTestId) || canRun : canRun;

// Overlay only the keys `extra` actually defines onto `base`, so a real §6 field
// (or a new structured field like amendments) wins while an absent one leaves the
// job-poll / MOCK_WORLD value in place rather than blanking it with undefined.
const overlayDefined = (base, extra) => {
  const merged = { ...base };
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined) merged[key] = value;
  }
  return merged;
};

// Resolve an environment id to its record. Order: an adopted client env in the
// store, then the harness backend, then the prebuilt template catalogue. An
// unknown id whose harness fetch 404s (and that no template claims) is notFound.
export function useEnvironment(envId) {
  const clientEnv = useEnvironmentsStore((s) => s.workspaceEnvs[envId]);
  const prebuilt = usePrebuiltEnvironments();

  // A client record only shadows the poll when it is genuinely client-only
  // (template/fork, or a mockMode build). A real build-adopted record
  // (origin:"harness") already holds the completed job's real snapshot, so the
  // client-first branch below serves it without a second fetch.
  const jobQuery = useQuery(harnessJobQuery(envId, { enabled: !clientEnv }));

  // §6 detail (gated by HARNESS_DETAIL_ENABLED). The job poll stays the live
  // heartbeat (§7); the detail carries no progress, so it is fetched once and
  // refetched when the job crosses `validating_scenarios` (sections appear) or a
  // terminal stage (results appear).
  const detailQuery = useQuery(
    harnessEnvironmentQuery(envId, { enabled: HARNESS_DETAIL_ENABLED && !clientEnv }),
  );
  const stage = jobQuery.data?.status?.stage;
  const detailRefetch = detailQuery.refetch;
  const crossed = useRef({ authored: false, terminal: false });
  useEffect(() => {
    if (!HARNESS_DETAIL_ENABLED || !stage) return;
    const authoredIdx = stages.indexOf("validating_scenarios");
    const idx = stages.indexOf(stage);
    if (!crossed.current.authored && idx >= 0 && authoredIdx >= 0 && idx >= authoredIdx) {
      crossed.current.authored = true;
      detailRefetch();
    }
    if (!crossed.current.terminal && terminalStages.has(stage)) {
      crossed.current.terminal = true;
      detailRefetch();
    }
  }, [stage, detailRefetch]);

  const detail = useMemo(
    () => (detailQuery.data ? harnessDetailToEnvironment(detailQuery.data) : null),
    [detailQuery.data],
  );

  const harness = useMemo(
    () => (jobQuery.data ? harnessJobToEnvironment(jobQuery.data) : null),
    [jobQuery.data],
  );

  const templateEnv = useMemo(
    () => prebuilt.data?.find((tpl) => tpl.id === envId) || null,
    [prebuilt.data, envId],
  );

  if (clientEnv) {
    return {
      env: clientEnv,
      source: "client",
      bootstrapState: undefined,
      notFound: false,
      isLoading: false,
    };
  }

  if (harness || (HARNESS_DETAIL_ENABLED && detail)) {
    // Prefer the real §6 detail once authoring is done: overlay its defined
    // fields onto the job-poll env (which keeps buildProgress + the MOCK_WORLD
    // fill for anything §6 has not authored). While still building, or when the
    // detail endpoint is unavailable, this is exactly the previous job-poll path.
    const detailReady = Boolean(detail?.env?.detailReady);
    const base = harness?.env ?? {};
    const env = detailReady ? overlayDefined(base, detail.env) : harness?.env ?? detail?.env;

    // Gate the bootstrap on the detail settling so a slightly-later §6 success is
    // not lost to useEnvState's write-once seed: use the real detail state when
    // ready; else fall back to the job-derived state only once the detail query
    // has resolved (success or error). While the detail is still in flight on a
    // finished env, hold the bootstrap (undefined) for the brief window.
    const detailSettled = !HARNESS_DETAIL_ENABLED || !detailQuery.isPending;
    let bootstrapState;
    if (detailReady) {
      bootstrapState = detail.envState;
    } else if (harness) {
      bootstrapState = detailSettled
        ? harnessEnvState(jobQuery.data, harness.world)
        : undefined;
    }

    return {
      env,
      source: "harness",
      bootstrapState,
      detailReady,
      notFound: false,
      isLoading: false,
    };
  }

  if (templateEnv) {
    return {
      env: templateEnv,
      source: "template",
      bootstrapState: undefined,
      notFound: false,
      isLoading: false,
    };
  }

  const notFound =
    isJobNotFound(jobQuery.error) &&
    prebuilt.isSuccess &&
    !templateEnv;

  return {
    env: null,
    source: null,
    bootstrapState: undefined,
    notFound,
    // A non-404 failure to resolve the id (the job fetch errored and no client or
    // template record covers it). Surfaced so the workspace renders a recoverable
    // error state instead of an endless blank placeholder.
    error: notFound ? null : jobQuery.error || null,
    refetch: jobQuery.refetch,
    isLoading: jobQuery.isPending || prebuilt.isPending,
  };
}
