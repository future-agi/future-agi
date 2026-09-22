import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

/**
 * The dedicated environments resource, mirroring `src/api/harness/harness.js`.
 * List is paginated ({ count, next, previous, total_pages, current_page,
 * results }); delete returns 204 and also cancels a live run server-side.
 *
 * Detail (§6), rename (§8) and remove-evaluation (§9) come from the updated
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

// §6 detail: the full environment (overview / contract / world / scenarios /
// evaluations / settings). Sections are null until the stage that produces them
// finishes, so every consumer must be null-tolerant.
export const getHarnessEnvironment = async (id) =>
  (await axios.get(environmentPath(id))).data;

// §8 rename: `name` is the only editable field. The response is the full §6
// detail body with `overview.name` updated, so callers can seed the detail
// cache from it rather than refetching.
export const renameHarnessEnvironment = async (id, name) =>
  (await axios.patch(environmentPath(id), { name })).data;

// §9 remove an applied evaluation (soft delete). Returns 204; the caller must
// re-fetch §6 and read `evaluations.selected` rather than removing locally.
export const deleteAppliedEvaluation = async (id, evalConfigId) =>
  (await axios.delete(environmentEvaluationPath(id, evalConfigId))).data;
