import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { getHarnessJob } from "src/api/harness/harness";
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
import { usePrebuiltEnvironments } from "./prebuilt";

// The harness detail poll cadence, matching HarnessDetail's own 2s tick.
const REFETCH_MS = 2000;

// The app's axios interceptor rejects with a customError carrying the HTTP
// status at `statusCode` (see utils/axios.js) — NOT at `response.status`, which
// is undefined on the rejected value. Read `statusCode` first, tolerating a raw
// axios error too.
export const errorStatus = (error) => error?.statusCode ?? error?.response?.status;

// A 404 (unknown / purged job id) is what puts the workspace into its not-found
// state, so it must never be retried.
const isJobNotFound = (error) => errorStatus(error) === 404;

// Poll until the job reaches a terminal stage — and stop dead on any error. A
// failed or unknown query has no stage to inspect, so without this guard the
// cadence stayed at 2s and a broken job hammered the endpoint forever.
export const jobRefetchInterval = (query) => {
  if (query?.state?.error) return false;
  return terminalStages.has(query?.state?.data?.status?.stage) ? false : REFETCH_MS;
};

// A voice transport in the detected connectors wins, same rule as the My
// Environments row mapping.
const agentTypeFor = (connectors = []) =>
  connectors.some((name) => VOICE_CONNECTORS.includes(name)) ? "voice" : "text";

// Parse the run's stage_outputs (kinds contract / environment / scenarios — see
// HarnessDetail's StageOutput) into world fields. Returns null when nothing is
// parseable. Only non-empty fields are set, so a field the run has not produced
// yet stays absent and its panel renders its own empty state.
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
  const scenarios = Array.isArray(scenariosOut) ? scenariosOut : [];

  const world = {};
  if (tools.length) world.tools = tools;
  if (rules.length) world.rules = rules;
  if (description) world.description = description;
  if (services.length) world.seed = { tables: [], services };
  if (scenarios.length) world.scenarios = scenarios;

  return Object.keys(world).length ? world : null;
}

// Raw scenario stage-output rows carry { name, instruction, use_case }; the
// scenario table reads the derived pool shape. Map them so the harness path
// renders the same table the client path does.
const scenarioFromOutput = (row) => ({
  id: row.name,
  name: row.name,
  title: row.name,
  task: row.instruction,
  situation: row.instruction,
  useCase: row.use_case || "Generated test case",
  expected: "",
  persona: null,
});

// The initial per-env state for an environment that only exists in the harness
// backend: an endpoint agent, the scenarios the run emitted and v1 versions.
// Real runs arrive from the executions API.
//
// Scenarios come only from the run's own scenarios stage output. A job that has
// not emitted them yet bootstraps with none — filling the gap from the derived
// fixture pool put invented scenarios on a real environment and, because the
// bootstrap was persisted, they outlived the real ones arriving.
export function harnessEnvState(item, world) {
  const job = item?.job || {};
  const status = item?.status || {};
  const resolved = world || {};

  const scenarios = (resolved.scenarios || []).map(scenarioFromOutput);

  const version = { label: "v1", note: "First run of this environment.", createdAt: status.created_at };

  return {
    agent: {
      typeId: job.agent?.connector || "auto",
      via: "endpoint",
      values: {},
      connectedAt: status.created_at,
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
// world fields are whatever the run's own stage outputs carry, and nothing else:
// a real environment must never be filled in from a fixture.
export function harnessJobToEnvironment(item) {
  const job = item?.job || {};
  const status = item?.status || {};
  const world = stageOutputsToWorld(item?.stage_outputs) ?? {};

  const env = {
    id: job.job_id,
    name: environmentName(job),
    agentType: agentTypeFor(item?.credentials?.detected_connectors),
    status: stageToStatus(status.stage),
    buildStatus: buildStatusFor(status.stage),
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
  };

  return { env, world };
}

// Header Run-simulation gating: a built harness job can run through the product
// bridge as soon as it has a run-test id, even before the client canRun is met.
export const canRunHeader = (source, env, canRun) =>
  source === "harness" ? Boolean(env?.platform?.runTestId) || canRun : canRun;

// Resolve an environment id to its record. Order: an adopted client env in the
// store, then the harness backend, then the prebuilt template catalogue. An
// unknown id whose harness fetch 404s (and that no template claims) is notFound.
export function useEnvironment(envId) {
  const clientEnv = useEnvironmentsStore((s) => s.workspaceEnvs[envId]);
  const prebuilt = usePrebuiltEnvironments();

  const jobQuery = useQuery({
    queryKey: ["harness-job", envId],
    queryFn: () => getHarnessJob(envId),
    enabled: Boolean(envId) && !clientEnv,
    retry: (failureCount, error) => !isJobNotFound(error) && failureCount < 3,
    refetchInterval: jobRefetchInterval,
  });

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

  if (harness) {
    return {
      env: harness.env,
      source: "harness",
      bootstrapState: harnessEnvState(jobQuery.data, harness.world),
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

  const notFound = isJobNotFound(jobQuery.error) && prebuilt.isSuccess && !templateEnv;

  return {
    env: null,
    source: null,
    bootstrapState: undefined,
    notFound,
    isLoading: jobQuery.isPending || prebuilt.isPending,
  };
}
