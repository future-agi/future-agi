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

export const listHarnessJobs = async () => (await axios.get(jobsPath())).data;
export const createHarnessJob = async (payload) =>
  (await axios.post(jobsPath(), payload)).data;
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
export const getHarnessJob = async (id) => (await axios.get(jobPath(id))).data;
export const cancelHarnessJob = async (id) =>
  (await axios.post(cancelPath(id))).data;
export const adjustHarnessJob = async (id, payload) =>
  (await axios.post(adjustPath(id), payload)).data;
