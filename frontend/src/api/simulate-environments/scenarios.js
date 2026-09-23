import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

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

export const listScenarios = async (jobId, params = {}) =>
  (
    await axios.get(scenariosPath(jobId), {
      params,
      paramsSerializer: serializeScenarioParams,
    })
  ).data;

// Amend one job's scenarios — edit (set_field / set_persona) and delete/bulk
// delete (drop). The body is { rework, changes:[...] }; the response is
// { receipts:[{ scenario, outcome, why }] }. There is no create route.
export const amendScenarios = async (jobId, body) =>
  (await axios.post(scenariosAmendPath(jobId), body)).data;

// The coverage cross-tab for one job — separate from the list because the grid
// does not change with the page but does change with the filter. Takes the same
// search + object-style filters as the list, plus row_axis / col_axis.
export const scenarioCoverage = async (jobId, params = {}) =>
  (
    await axios.get(scenariosCoveragePath(jobId), {
      params,
      paramsSerializer: serializeScenarioParams,
    })
  ).data;

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
    _raw: row,
  };
};
