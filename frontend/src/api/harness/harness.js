import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

const jobsPath = () => apiPath("/simulate/api/harness-jobs/");
const jobPath = (id) => apiPath("/simulate/api/harness-jobs/{id}/", { id });
const cancelPath = (id) =>
  apiPath("/simulate/api/harness-jobs/{id}/cancel/", { id });
const adjustPath = (id) =>
  apiPath("/simulate/api/harness-jobs/{id}/adjust/", { id });
const preflightPath = () => apiPath("/simulate/api/harness-jobs/preflight/");
const sourcesPath = () => apiPath("/simulate/api/harness-jobs/sources/");
const secretFilesPath = () =>
  apiPath("/simulate/api/harness-jobs/secret-files/");
const secretValuesPath = () =>
  apiPath("/simulate/api/harness-jobs/secret-values/");

export const listHarnessJobs = async () => (await axios.get(jobsPath())).data;

// `crypto.randomUUID` is not available in every browser/webview context.  This
// key is only used to make a create request idempotent; failing to construct it
// must never prevent the request from leaving the browser.
export const harnessIdempotencyKey = () => {
  const randomUUID = globalThis.crypto?.randomUUID;
  if (typeof randomUUID === "function") {
    return randomUUID.call(globalThis.crypto);
  }
  return `harness-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2)}`;
};

export const createHarnessJob = async (
  payload,
  idempotencyKey = harnessIdempotencyKey(),
) =>
  (
    await axios.post(jobsPath(), payload, {
      headers: { "Idempotency-Key": idempotencyKey },
    })
  ).data;
export const preflightHarnessJob = async (payload) =>
  (await axios.post(preflightPath(), payload)).data;
export const uploadHarnessSource = async (formData, onUploadProgress) =>
  (
    await axios.post(sourcesPath(), formData, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress,
      timeout: 300000,
    })
  ).data;
export const uploadHarnessSecretFile = async (formData) =>
  (
    await axios.post(secretFilesPath(), formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 60000,
    })
  ).data;
export const storeHarnessSecretValues = async (environmentValues) =>
  (
    await axios.post(secretValuesPath(), {
      environment_values: environmentValues,
    })
  ).data;
export const getHarnessJob = async (id) => (await axios.get(jobPath(id))).data;
// The contract marks this endpoint runtimeRequestValidation: true against
// HarnessJobAction, so it must be sent an object. Posting no body at all makes the
// validator parse `undefined`, which fails before the request ever leaves the browser.
// `reason` is optional, so it is omitted rather than sent empty.
export const cancelHarnessJob = async (id, reason) => {
  const body = {};
  const trimmed = typeof reason === "string" ? reason.trim() : "";
  if (trimmed) body.reason = trimmed.slice(0, 500);
  return (await axios.post(cancelPath(id), body)).data;
};
export const adjustHarnessJob = async (id, payload) =>
  (await axios.post(adjustPath(id), payload)).data;
