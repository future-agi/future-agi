import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

/**
 * The dedicated environments resource, mirroring `src/api/harness/harness.js`.
 * List is paginated ({ count, next, previous, total_pages, current_page,
 * results }); delete returns 204 and also cancels a live run server-side.
 */

const environmentsPath = () => apiPath("/simulate/api/harness-environments/");
const environmentPath = (id) =>
  apiPath("/simulate/api/harness-environments/{id}/", { id });

export const listHarnessEnvironments = async ({ page, limit } = {}) => {
  const params = {};
  if (page != null) params.page = page;
  if (limit != null) params.limit = limit;
  return (await axios.get(environmentsPath(), { params })).data;
};

export const deleteHarnessEnvironment = async (id) =>
  (await axios.delete(environmentPath(id))).data;
