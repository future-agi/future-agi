import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

/**
 * The dedicated environments resource, mirroring `src/api/harness/harness.js`.
 * List is paginated ({ count, next, previous, total_pages, current_page,
 * results }); delete returns 204 and also cancels a live run server-side.
 *
 * Detail (§5), rename (§8) and remove-evaluation (§4) come from the updated
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

// §5 detail: the full environment (overview / contract / world / scenarios /
// evaluations / settings). Sections are null until the stage that produces them
// finishes, so every consumer must be null-tolerant.
export const getHarnessEnvironment = async (id) =>
  (await axios.get(environmentPath(id))).data;

// §8 rename: `name` is the only editable field. The response is the full §5
// detail body with `overview.name` updated, so callers can seed the detail
// cache from it rather than refetching.
export const renameHarnessEnvironment = async (id, name) =>
  (await axios.patch(environmentPath(id), { name })).data;

// §4 remove an applied evaluation (soft delete). Returns 204; the caller must
// re-fetch §5 and read `evaluations.selected` rather than removing locally.
export const deleteAppliedEvaluation = async (id, evalConfigId) =>
  (await axios.delete(environmentEvaluationPath(id, evalConfigId))).data;

// §2 the evals this environment can still add — the catalogue filtered to its
// modality and minus what is already selected. Each entry is the full §1
// shape (name, description, source, tags, required_keys, agent_type,
// modality, credits_per_run, charges_judge_tokens, inputs[]). Every entry is
// addable as-is (no client filtering).
export const getAvailableEvaluations = async (id) =>
  (await axios.get(environmentEvaluationsAvailablePath(id))).data;

// §3 add one evaluation by name. The body is `{ name }` only — the input
// mapping is resolved server-side by modality. The 201 body is the full §5
// detail, already updated, so the caller seeds the detail cache from it.
export const addEvaluation = async (id, name) =>
  (await axios.post(environmentEvaluationsPath(id), { name })).data;

const runEvaluationsPath = (id, executionId) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/runs/{execution_id}/evaluations/",
    { id, execution_id: executionId },
  );

// §6 add one evaluation from inside a run. The body is `{ name }` only, exactly
// as §3's: the endpoint first does what §3 does (same refusals, same idempotency)
// and only then queues grading for this run's finished calls. The 202 body is
// the five counts — { queued, skipped_existing, skipped_in_flight,
// skipped_pending, completed_calls } — NOT the environment detail, so the caller
// refetches the detail rather than seeding it from this response. The path is
// in the generated Swagger surface as of TH-8046 (backend PR #3015).
export const addRunEvaluation = async (id, executionId, name) =>
  (await axios.post(runEvaluationsPath(id, executionId), { name })).data;
