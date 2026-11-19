import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { listHarnessEnvironments } from "src/api/simulate-environments/harnessEnvironments";
import { envStatusFor } from "src/sections/simulate/environments/helpers/harnessJobToRow";

// Agent detail page data (agent pages contract, sections 2-6).

const unwrap = (data) => data?.result ?? data;

export const agentDetailQueryKey = (agentId) => ["agents", "detail", agentId];

const toVersionRef = (v) => (v ? { id: v.id, number: v.version_number } : null);

export const toRun = (raw) => ({
  kind: raw.kind,
  testExecutionId: raw.test_execution_id,
  runTestId: raw.run_test_id,
  runTestName: raw.run_test_name ?? "",
  runNumber: raw.run_number,
  status: raw.status,
  environment: raw.environment ?? null,
  agentVersion: toVersionRef(raw.agent_version),
  passed: raw.outcome_passed ?? null,
  failed: raw.outcome_failed ?? null,
  errored: raw.outcome_errored ?? null,
  noScore: raw.outcome_no_score ?? null,
  skipped: raw.outcome_skipped ?? null,
  // The run's trial pass, as its header and the environment's runs list read it; null when no trial passed or failed.
  passRate: raw.trial_pass_rate,
  // The trials behind that rate, and those left out of it.
  trialsPassed: raw.trials_passed,
  trialsEvaluated: raw.trials_evaluated,
  trialsInconclusive: raw.trials_inconclusive,
  trialsExcluded: raw.trials_excluded_not_comparable,
  scored: raw.scored,
  startedAt: raw.started_at ?? null,
  completedAt: raw.completed_at ?? null,
});

export const toVersion = (v) => ({
  id: v.id,
  number: v.version_number,
  label: v.version_name_display || `v${v.version_number}`,
  isActive: !!v.is_active,
  isLatest: !!v.is_latest,
  commitMessage: v.commit_message ?? null,
  createdAt: v.created_at ?? null,
});

export const mapAgentDetail = (raw) => ({
  id: raw.id,
  name: raw.agent_name,
  assistantId: raw.assistant_id || null,
  provider: raw.provider || null,
  createdAt: raw.created_at ?? null,
  activeVersionId: raw.active_version?.id ?? null,
  versionCount: raw.version_count,
  agentKind: raw.agent_kind ?? null,
  source: raw.source ?? null,
  environmentCount: raw.environment_count,
  observabilityEnabled: raw.observability_enabled,
  observabilityProviderId: raw.observability_provider ?? null,
});

const toStoredAgent = (d) => ({
  agent: d.agent,
  sdkVersion: d.sdk_version,
  storedAt: d.stored_at,
  setupStale: !!d.setup_stale,
});

export const mapVersionDetail = (raw) => ({
  id: raw.id,
  label: raw.version_name_display || `v${raw.version_number}`,
  number: raw.version_number,
  isActive: !!raw.is_active,
  isLatest: !!raw.is_latest,
  commitMessage: raw.commit_message || null,
  createdAt: raw.created_at ?? null,
  agentKind: raw.agent_kind ?? null,
  apiKey: raw.api_key ?? null,
  config: raw.configuration_snapshot ?? {},
  // The workers its newest built environment declares, read-only on the Definition tab.
  participants: raw.participants,
});

export function useAgentDetail(agentId) {
  return useQuery({
    queryKey: agentDetailQueryKey(agentId),
    queryFn: async () =>
      unwrap(
        (await axios.get(endpoints.agentDefinitions.detail(agentId))).data,
      ),
    select: mapAgentDetail,
    enabled: !!agentId,
  });
}

// The connection's last recorded setup problem (`configuration_health` on the
// observability provider), or null when imports are healthy or the field is not
// served. Shape: { status, reason_code, first_failed_at, last_failed_at,
// failure_count, next_retry_at }.
export function useProviderConfigurationHealth(providerId) {
  return useQuery({
    queryKey: ["agents", "provider-health", providerId],
    queryFn: async () =>
      unwrap(
        (
          await axios.get(
            endpoints.agentDefinitions.observabilityProvider(providerId),
          )
        ).data,
      ),
    select: (d) => d?.configuration_health ?? null,
    enabled: !!providerId,
    // A link to a deleted provider answers 400; the warning is optional, so
    // that is not worth an error toast on the agent page.
    meta: { errorHandled: true },
  });
}

export function useAgentRuns(agentId, { limit = 5 } = {}) {
  return useQuery({
    queryKey: ["agents", "runs", agentId, limit],
    queryFn: async () =>
      unwrap(
        (
          await axios.get(endpoints.agentDefinitions.runs(agentId), {
            params: { limit },
          })
        ).data,
      ),
    select: (d) => (d?.results ?? []).map(toRun),
    enabled: !!agentId,
  });
}

export function useAgentVersion(agentId, versionId) {
  return useQuery({
    queryKey: ["agents", "version", agentId, versionId],
    queryFn: async () =>
      unwrap(
        (
          await axios.get(
            endpoints.agentDefinitions.versionDetail(agentId, versionId),
          )
        ).data,
      ),
    select: mapVersionDetail,
    enabled: !!agentId && !!versionId,
  });
}

// The Agent a version stored (GET .../versions/{v}/agent/). The endpoint
// answers 404 when the version stored none, which reads as null here; any
// other failure is an error.
export function useStoredAgent(agentId, versionId, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["agents", "stored-agent", agentId, versionId],
    queryFn: async () => {
      try {
        return unwrap(
          (
            await axios.get(
              endpoints.agentDefinitions.storedAgent(agentId, versionId),
            )
          ).data,
        );
      } catch (e) {
        if (e?.statusCode === 404) return null;
        throw e;
      }
    },
    select: (d) => (d ? toStoredAgent(d) : null),
    enabled: !!agentId && !!versionId && enabled,
  });
}

export function useAgentEnvironments(agentId) {
  return useQuery({
    queryKey: ["agents", "environments", agentId],
    queryFn: () =>
      listHarnessEnvironments({
        agentDefinitionId: agentId,
        page: 1,
        limit: 100,
      }),
    select: (d) =>
      (d?.results ?? []).map((e) => ({
        id: e.id,
        name: e.name,
        // Mapped like the My Environments table: a built environment is Ready.
        status: envStatusFor(e.stage, e.status),
        lastRun: e.last_run ? toRun(e.last_run) : null,
      })),
    enabled: !!agentId,
  });
}

export function useSetObservability(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ enabled }) =>
      axios.put(endpoints.agentDefinitions.edit(agentId), {
        observability_enabled: enabled,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: agentDetailQueryKey(agentId) }),
  });
}
