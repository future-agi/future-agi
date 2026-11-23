import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { isScenarioSampleMode } from "./scenariosSampleMode";

// Dev sample mode (?scnSample=1): serve the in-repo captured 20-row suite from
// the fixtures emulator instead of the live endpoints. Dynamically imported so
// the emulator and its sample JSON never enter the production bundle, and given
// a small latency so the loading states still show. Deleted with the emulator
// on the live flip.
const SAMPLE_LATENCY_MS = 250;
const withSampleLatency = (value) =>
  new Promise((resolve) => setTimeout(() => resolve(value), SAMPLE_LATENCY_MS));
const sampleEmulator = () => import("./_fixtures/scenariosFixtures");

/**
 * The scenarios resource for one harness job — list, coverage and amend.
 * `env.id` is the job id, and the workspace passes it as `jobId`.
 *
 * These call the real endpoints. Their params (page, limit, search, group_by,
 * ordering, object-style filters) are read by hand server-side and are not yet
 * in the generated Swagger surface, so `apiPath()` for these routes THROWS
 * until the backend lands them and `yarn contracts:generate` adds them to the
 * surface — the sanctioned "write it in final form now, it starts working when
 * the surface includes it" pattern (see harnessEnvironments.js).
 */

const scenariosPath = (jobId) =>
  apiPath("/simulate/api/harness-jobs/{id}/scenarios/", { id: jobId });
export const scenariosCoveragePath = (jobId) =>
  apiPath("/simulate/api/harness-jobs/{id}/scenarios/coverage/", { id: jobId });
export const scenariosAmendPath = (jobId) =>
  apiPath("/simulate/api/harness-jobs/{id}/scenarios/amend/", { id: jobId });

// Serialise params so a repeated (array) key becomes repeated query params
// (?persona.accent=Canadian&persona.accent=Indian), dotted keys pass through,
// and undefined/null/empty values are dropped — but the empty string is kept
// (group_by="" means "no grouping" and must be sent).
export const serializeScenarioParams = (params = {}) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      value
        .filter((v) => v !== undefined && v !== null && v !== "")
        .forEach((v) => search.append(key, String(v)));
    } else {
      search.append(key, String(value));
    }
  }
  return search.toString();
};

export const listScenarios = async (jobId, params = {}) => {
  if (isScenarioSampleMode()) {
    const { queryScenarioFixture } = await sampleEmulator();
    return withSampleLatency(queryScenarioFixture(params));
  }
  return (
    await axios.get(scenariosPath(jobId), {
      params,
      paramsSerializer: serializeScenarioParams,
    })
  ).data;
};

// Amend one job's scenarios — edit (set_field / set_persona) and delete/bulk
// delete (drop). The body is { rework, changes:[...] }; the response is
// { receipts:[{ scenario, outcome, why }] }. There is no create route.
export const amendScenarios = async (jobId, body) => {
  if (isScenarioSampleMode()) {
    const { amendScenarioFixture } = await sampleEmulator();
    return withSampleLatency(amendScenarioFixture(body));
  }
  return (await axios.post(scenariosAmendPath(jobId), body)).data;
};

// The coverage cross-tab for one job — separate from the list because the grid
// does not change with the page but does change with the filter. Takes the same
// search + object-style filters as the list, plus row_axis / col_axis.
export const scenarioCoverage = async (jobId, params = {}) => {
  if (isScenarioSampleMode()) {
    const { coverageScenarioFixture } = await sampleEmulator();
    return withSampleLatency(coverageScenarioFixture(params));
  }
  return (
    await axios.get(scenariosCoveragePath(jobId), {
      params,
      paramsSerializer: serializeScenarioParams,
    })
  ).data;
};

const validatePath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/scenarios/validate/", {
    id: envId,
  });
const runningValidationPath = (envId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/scenarios/validate/running/",
    {
      id: envId,
    },
  );
const archivePath = (envId, key) =>
  apiPath("/simulate/api/harness-environments/{id}/scenarios/{key}/archive/", {
    id: envId,
    key,
  });
const restorePath = (envId, key) =>
  apiPath("/simulate/api/harness-environments/{id}/scenarios/{key}/restore/", {
    id: envId,
    key,
  });
const sourceChangesPath = (envId, versionId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/versions/environment/{version_id}/source-changes/",
    { id: envId, version_id: versionId },
  );

// The archive note the server keeps is at most 255 characters.
export const ARCHIVE_REASON_MAX = 255;

// Prove the default pair's scenarios: those with no current proof, only `keys`, or all with `reprove_all`.
// 200 answers `ready` (nothing to prove) or `unavailable`; 202 a running job to poll.
export const validateScenarios = async (
  envId,
  { keys, reproveAll = false } = {},
) => {
  const body = {};
  if (keys?.length) body.keys = keys;
  if (reproveAll) body.reprove_all = true;
  return (await axios.post(validatePath(envId), body)).data;
};

export const getScenarioValidation = async (envId, jobId) =>
  (await axios.get(validatePath(envId), { params: { job_id: jobId } })).data;

// The default pair's validation still running, so a page opened later follows it instead of starting another.
export const getRunningScenarioValidation = async (envId) =>
  (await axios.get(runningValidationPath(envId))).data;

export const archiveScenario = async (envId, key, reason = "") => {
  const trimmed = reason.trim().slice(0, ARCHIVE_REASON_MAX);
  return (
    await axios.post(
      archivePath(envId, key),
      trimmed ? { reason: trimmed } : {},
    )
  ).data;
};

export const restoreScenario = async (envId, key) =>
  (await axios.post(restorePath(envId, key), {})).data;

export const getSourceChanges = async (envId, versionId) =>
  (await axios.get(sourceChangesPath(envId, versionId))).data;

// Map a server row to the SCENARIO_SHAPE the table/list render, carrying the
// raw row on `_raw` for the Phase-2 edit drawer. The persona is normalised to
// the shape's camelCase keys; coverage/keywords and the run fields ride along
// untouched.
export const scenarioFromApi = (row = {}) => {
  const persona = row.persona || null;
  return {
    id: row.id,
    scenarioId: row.scenario_id ?? null,
    scenarioKey: row.scenario_key,
    number: row.number,
    name: row.name,
    useCase: row.use_case,
    situation: row.instruction,
    task: row.instruction,
    expected: row.tests,
    outcome: row.tests,
    conversationBranch: row.branch,
    branchCategory: row.branch,
    subTasks: Array.isArray(row.sub_goals) ? row.sub_goals : [],
    callActors: Array.isArray(row.actors) ? row.actors : [],
    // {code, message} when the scenario cannot work as set, else null.
    doorProblem: row.door_problem,
    faults: Array.isArray(row.faults) ? row.faults : [],
    persona: persona
      ? {
          name: persona.name,
          accent: persona.accent,
          languages: persona.languages,
          ageGroup: persona.age_group,
          gender: persona.gender,
          location: persona.location,
          personality: persona.personality,
          communicationStyle: persona.communication_style,
          occupation: persona.occupation,
          initialMessage: persona.initial_message,
        }
      : null,
    coverage: row.coverage,
    keywords: Array.isArray(row.keywords) ? row.keywords : [],
    backgroundNoise: row.background_noise,
    maxTurns: row.max_turns,
    status: row.status,
    callExecutionId: row.call_execution_id ?? null,
    group: row.group,
    // "added" | "changed" | "unchanged" while the list shows unsaved changes.
    draftChange: row.draft_change ?? null,
    // Draft, Validated, Active or Archived on the versions in view, with why; null when unversioned.
    lifecycle: row.lifecycle ?? null,
    // A must-pass case: a failure here blocks the release.
    critical: row.lifecycle?.must_pass === true,
    sources: Array.isArray(row.lifecycle?.sources) ? row.lifecycle.sources : [],
    _raw: row,
  };
};
