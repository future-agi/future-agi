import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

const jobsPath = () => apiPath("/simulate/api/harness-jobs/");
const jobPath = (id) => apiPath("/simulate/api/harness-jobs/{id}/", { id });
const cancelPath = (id) =>
  apiPath("/simulate/api/harness-jobs/{id}/cancel/", { id });
const preflightPath = () => apiPath("/simulate/api/harness-jobs/preflight/");

export const listHarnessJobs = async () => (await axios.get(jobsPath())).data;
export const createHarnessJob = async (payload) =>
  (await axios.post(jobsPath(), payload)).data;
export const preflightHarnessJob = async (payload) =>
  (await axios.post(preflightPath(), payload)).data;
export const getHarnessJob = async (id) => (await axios.get(jobPath(id))).data;
export const cancelHarnessJob = async (id) =>
  (await axios.post(cancelPath(id))).data;
