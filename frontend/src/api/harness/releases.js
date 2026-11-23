import { useQuery } from "@tanstack/react-query";

import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

// Released environments, adaptation and run reports (environment-releases.md §7.5-§7.7).

export const listHarnessReleases = async () =>
  (await axios.get(apiPath("/simulate/api/harness-releases/"))).data;

// Takes `{suite_id, agent_version_id, agent, name, idempotency_key}` and answers 202 with the
// hosted job it started; a completed adaptation creates the composed environment.
export const createHarnessAdaptation = async (payload) =>
  (await axios.post(apiPath("/simulate/api/harness-adaptations/"), payload))
    .data;

// Takes `{suite_id, name, instructions, model, idempotency_key}` and answers 201 with the composed
// environment that runs our own agent on the suite (§6.4); there is no adaptation to wait for.
export const createHarnessHostedAgent = async (payload) =>
  (await axios.post(apiPath("/simulate/api/harness-hosted-agents/"), payload))
    .data;

// Takes `{name, requirements, modality, template_suite_id?, document_ids?, idempotency_key}` and
// answers 202 with the design job (§7.9): its builder designs and seals the environment.
export const createHarnessDesign = async (payload) =>
  (
    await axios.post(
      apiPath("/simulate/api/harness-environment-designs/"),
      payload,
    )
  ).data;

// One document for a design: answers 201 `{id, name, content_type, sha256, size}`.
export const uploadHarnessDesignDocument = async (file) => {
  const form = new FormData();
  form.append("file", file);
  return (
    await axios.post(
      apiPath("/simulate/api/harness-environment-designs/documents/"),
      form,
    )
  ).data;
};

// A design that stopped at an unanswered question carries on as a new design job.
export const continueHarnessDesign = async (id, payload) =>
  (
    await axios.post(
      apiPath("/simulate/api/harness-environment-designs/{id}/continue/", {
        id,
      }),
      payload,
    )
  ).data;

export const getHarnessRunReport = async (id, runId, baseline) =>
  (
    await axios.get(
      apiPath("/simulate/api/harness-environments/{id}/runs/{run_id}/report/", {
        id,
        run_id: runId,
      }),
      { params: baseline ? { baseline } : {} },
    )
  ).data;

export const HARNESS_RELEASES_KEY = ["harness-releases"];

export function useHarnessReleases({ enabled = true } = {}) {
  return useQuery({
    queryKey: HARNESS_RELEASES_KEY,
    queryFn: listHarnessReleases,
    select: (data) => (Array.isArray(data?.results) ? data.results : []),
    enabled,
    meta: { errorHandled: true },
  });
}

export const harnessRunReportKey = (envId, runId, baseline) => [
  "harness-run-report",
  envId,
  runId,
  baseline || null,
];

export function useHarnessRunReport(
  envId,
  runId,
  baseline,
  { live = false } = {},
) {
  return useQuery({
    queryKey: harnessRunReportKey(envId, runId, baseline),
    queryFn: () => getHarnessRunReport(envId, runId, baseline),
    enabled: Boolean(envId && runId),
    // Trials land while the run is live; the report has no push channel of its own.
    refetchInterval: live ? 5000 : false,
    retry: (count, error) =>
      error?.statusCode !== 409 && error?.statusCode !== 404 && count < 2,
    meta: { errorHandled: true },
  });
}
