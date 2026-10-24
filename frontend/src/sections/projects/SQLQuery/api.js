import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

export const sqlApi = {
  catalog: (projectId) =>
    axios
      .get(apiPath("/tracer/sql-query/catalog/"), {
        params: { project_id: projectId },
      })
      .then((r) => r.data),
  run: (data, options = {}) =>
    axios
      .post(apiPath("/tracer/sql-query/run/"), data, {
        timeout: 15_000,
        ...options,
      })
      .then((r) => r.data),
  // Advisory index estimate: never admits or blocks a run.
  estimate: (data, signal) =>
    axios
      .post(apiPath("/tracer/sql-query/estimate/"), data, {
        signal,
        timeout: 10_000,
      })
      .then((r) => r.data),
  runSaved: (data, options = {}) =>
    axios
      .post(apiPath("/tracer/sql-query/run-saved/"), data, {
        timeout: 15_000,
        ...options,
      })
      .then((r) => r.data),
  result: (projectId, id, signal) =>
    axios
      .get(apiPath("/tracer/sql-query/{id}/", { id }), {
        params: { project_id: projectId },
        signal,
        timeout: 15_000,
      })
      .then((r) => r.data),
  cancel: (projectId, id) =>
    axios
      .post(
        apiPath("/tracer/sql-query/{id}/cancel/", { id }),
        {
          project_id: projectId,
        },
        { timeout: 15_000 },
      )
      .then((r) => r.data),
  history: (projectId, offset = 0) =>
    axios
      .get(apiPath("/tracer/sql-query/"), {
        params: { project_id: projectId, offset },
      })
      .then((r) => r.data),
  saved: (projectId, offset = 0) =>
    axios
      .get(apiPath("/tracer/saved-sql-query/"), {
        params: { project_id: projectId, offset },
      })
      .then((r) => r.data),
  getSaved: (projectId, id) =>
    axios
      .get(apiPath("/tracer/saved-sql-query/{id}/", { id }), {
        params: { project_id: projectId },
      })
      .then((r) => r.data),
  save: (data) =>
    axios.post(apiPath("/tracer/saved-sql-query/"), data).then((r) => r.data),
  update: (id, data) =>
    axios
      .patch(apiPath("/tracer/saved-sql-query/{id}/", { id }), data)
      .then((r) => r.data),
  remove: (projectId, id) =>
    axios.delete(apiPath("/tracer/saved-sql-query/{id}/", { id }), {
      params: { project_id: projectId },
    }),
  addWidget: (projectId, id, dashboardId) =>
    axios
      .post(apiPath("/tracer/saved-sql-query/{id}/add-widget/", { id }), {
        project_id: projectId,
        dashboard_id: dashboardId,
      })
      .then((r) => r.data),
  download: (projectId, id, format) =>
    axios
      .get(apiPath("/tracer/sql-query/{id}/download/", { id }), {
        params: { project_id: projectId, file_format: format },
        responseType: "blob",
      })
      .then((r) => r.data),
};

export function errorMessage(error) {
  const detail = error?.response?.data?.detail;
  return typeof detail === "string"
    ? detail
    : error?.message || "Query request failed. Please try again.";
}
