import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { listHarnessEnvironments } from "src/api/simulate-environments/harnessEnvironments";
import { envStatusFor } from "src/sections/simulate/environments/helpers/harnessJobToRow";

// Agent detail page data (agent pages contract, sections 2-6).

const unwrap = (data) => data?.result ?? data;

export const agentDetailQueryKey = (agentId) => ["agents", "detail", agentId];

export const passRateOf = ({ passed, failed, skipped }) => {
  if (passed == null) return null;
  const measured = passed + (failed ?? 0) + (skipped ?? 0);
  return measured ? Math.round((passed / measured) * 100) : null;
};

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
  skipped: raw.outcome_skipped ?? null,
  passRate: passRateOf({
    passed: raw.outcome_passed ?? null,
    failed: raw.outcome_failed,
    skipped: raw.outcome_skipped,
  }),
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
