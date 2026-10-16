import { useQuery } from "@tanstack/react-query";

import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

// Released environments, adaptation and composition (environment-releases.md §7.5-§7.7).

export const listHarnessReleases = async () =>
  (await axios.get(apiPath("/simulate/api/harness-releases/"))).data;

// Both take `{suite_id, agent_version_id, agent, name, idempotency_key}`. Adaptation answers 202
// with the hosted job it started; composition answers 201 `{id}`, the composed environment.
export const createHarnessAdaptation = async (payload) =>
  (await axios.post(apiPath("/simulate/api/harness-adaptations/"), payload))
    .data;

export const createHarnessComposition = async (payload) =>
  (await axios.post(apiPath("/simulate/api/harness-compositions/"), payload))
    .data;

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
