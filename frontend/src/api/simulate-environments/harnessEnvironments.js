import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

/**
 * The dedicated environments resource, mirroring `src/api/harness/harness.js`.
 * List is paginated ({ count, next, previous, total_pages, current_page,
 * results }); delete returns 204 and also cancels a live run server-side.
 *
 * Detail, rename and remove-evaluation come from the updated
 * contract. Detail and rename reuse the already-contracted
 * `/harness-environments/{id}/` path, so apiPath() accepts them today (the gate
 * validates the path template, not the method). The remove-evaluation path is
 * new: apiPath() throws `API path is not in generated contract` until the
 * backend lands the endpoint and `npm run contracts:generate` regenerates the
 * Swagger surface. That is intentional — the call is written in its final form
 * and starts working the moment the surface includes it, with no code change.
 */

const environmentsPath = () => apiPath("/simulate/api/harness-environments/");
const environmentPath = (id) =>
  apiPath("/simulate/api/harness-environments/{id}/", { id });
const environmentRunPath = (id) =>
  apiPath("/simulate/api/harness-environments/{id}/run/", { id });
const environmentEvaluationsPath = (id) =>
  apiPath("/simulate/api/harness-environments/{id}/evaluations/", { id });
const environmentEvaluationsAvailablePath = (id) =>
  apiPath("/simulate/api/harness-environments/{id}/evaluations/available/", { id });
const environmentEvaluationPath = (id, evalConfigId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/evaluations/{eval_config_id}/",
    { id, eval_config_id: evalConfigId },
  );

export const listHarnessEnvironments = async ({ page, limit } = {}) => {
  const params = {};
  if (page != null) params.page = page;
  if (limit != null) params.limit = limit;
  return (await axios.get(environmentsPath(), { params })).data;
};

export const deleteHarnessEnvironment = async (id) =>
  (await axios.delete(environmentPath(id))).data;

// Detail: the full environment (overview / contract / world / scenarios /
// evaluations / settings). Sections are null until the stage that produces them
// finishes, so every consumer must be null-tolerant.
export const getHarnessEnvironment = async (id) =>
  (await axios.get(environmentPath(id))).data;

export const runHarnessEnvironment = async (
  id,
  scenarioIds,
  trials,
  idempotencyKey,
) =>
  (
    await axios.post(
      environmentRunPath(id),
      { scenario_ids: scenarioIds, trials },
      { headers: { "Idempotency-Key": idempotencyKey } },
    )
  ).data;

// §8 rename: `name` is the only editable field. The response is the full §6
// detail body with `overview.name` updated, so callers can seed the detail
// cache from it rather than refetching.
export const renameHarnessEnvironment = async (id, name) =>
  (await axios.patch(environmentPath(id), { name })).data;

// Remove an applied evaluation (soft delete). Returns 204; the caller must
// re-fetch the detail and read `evaluations.selected` rather than removing
// locally.
export const deleteAppliedEvaluation = async (id, evalConfigId) =>
  (await axios.delete(environmentEvaluationPath(id, evalConfigId))).data;

// The evals this environment can still add — the catalogue filtered to its
// modality and minus what is already selected. Each entry is the full
// shape (name, description, source, tags, required_keys, agent_type,
// modality, credits_per_run, charges_judge_tokens, inputs[]). Every entry is
// addable as-is (no client filtering).
export const getAvailableEvaluations = async (id) =>
  (await axios.get(environmentEvaluationsAvailablePath(id))).data;

// Add one evaluation by name. The body is `{ name }` only — the input
// mapping is resolved server-side by modality. The 201 body is the full
// detail, already updated, so the caller seeds the detail cache from it.
export const addEvaluation = async (id, name) =>
  (await axios.post(environmentEvaluationsPath(id), { name })).data;

// Turn the tool-call judge on or off. PUT with the whole state of the switch;
// the 200 body is the full detail (`settings.enable_tool_evaluation`), so the
// caller seeds the detail cache from it. 409 turning it on for a voice
// environment whose agent has no version yet.
export const setToolCallEvaluation = async (id, enabled) =>
  (
    await axios.put(
      apiPath("/simulate/api/harness-environments/{id}/evaluations/tool-call/", { id }),
      { enable_tool_evaluation: enabled },
    )
  ).data;

const runEvaluationsPath = (id, executionId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/runs/{execution_id}/evaluations/",
    { id, execution_id: executionId },
  );

// Add one evaluation from inside a run. Same body as the environment-level
// add (`{ name }`), and does what it does first (same refusals, same
// idempotency) before queuing grading for the run's finished calls. The 202
// body is the five counts —
// { queued, skipped_existing, skipped_in_flight, skipped_pending,
// completed_calls } — not the environment detail, so there is nothing in
// this response to seed the applied list from.
export const addRunEvaluation = async (id, executionId, name) =>
  (await axios.post(runEvaluationsPath(id, executionId), { name })).data;
