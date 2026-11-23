import { keepPreviousData, useQuery } from "@tanstack/react-query";

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

// Takes `{suite_id, agent_version_id, pairing}` and answers how the agent fits the suite: its
// tools paired with the world's and the cases that pairing measures. Nothing is built.
export const fitHarnessRelease = async (payload) =>
  (await axios.post(apiPath("/simulate/api/harness-releases/fit/"), payload))
    .data;

export function useHarnessReleaseFit({ suiteId, agentVersionId, pairing }) {
  return useQuery({
    queryKey: ["harness-release-fit", suiteId, agentVersionId, pairing || null],
    queryFn: () =>
      fitHarnessRelease({
        suite_id: suiteId,
        agent_version_id: agentVersionId,
        ...(pairing ? { pairing } : {}),
      }),
    enabled: Boolean(suiteId && agentVersionId),
    // A changed pairing keeps the table in place while it is counted again.
    placeholderData: keepPreviousData,
    retry: false,
    meta: { errorHandled: true },
  });
}

// Review the design: what an adaptation takes from the ready world, the pairing, the cases it
// measures, its policy conflicts and where it stands (`state`). Polled until it is ready.
export const harnessAdaptationDesignKey = (id) => [
  "harness-adaptation-design",
  id,
];

export function useHarnessAdaptationDesign(id) {
  return useQuery({
    queryKey: harnessAdaptationDesignKey(id),
    queryFn: async () =>
      (
        await axios.get(
          apiPath("/simulate/api/harness-adaptations/{id}/design/", { id }),
        )
      ).data,
    enabled: Boolean(id),
    refetchInterval: (query) =>
      ["ready", "failed"].includes(query.state.data?.state) ? false : 5000,
    meta: { errorHandled: true },
  });
}

// Approve and build: answers the adaptation's approval once every case has been checked.
export const approveHarnessAdaptation = async (id) =>
  (
    await axios.post(
      apiPath("/simulate/api/harness-adaptations/{id}/approve/", { id }),
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
