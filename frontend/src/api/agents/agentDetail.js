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
  environmentVersion: toVersionRef(raw.environment_version),
  partial: raw.partial ?? null,
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
  isDraft: v.status === "draft",
  commitMessage: v.commit_message ?? null,
  createdAt: v.created_at ?? null,
  // From the version's side row; null for a version made before versioning.
  origin: v.origin ?? null,
  check: v.preflight_status ?? null,
  environmentsCount: v.environments_count ?? 0,
  source: v.source ?? null,
});

export const toPolicy = (value) => (value === "pinned" ? "pinned" : "follow");

export const mapAgentDetail = (raw) => ({
  id: raw.id,
  name: raw.agent_name,
  assistantId: raw.assistant_id || null,
  provider: raw.provider || null,
  createdAt: raw.created_at ?? null,
  activeVersionId: raw.active_version?.id ?? null,
  activeVersionNumber: raw.active_version?.version_number ?? null,
  versionCount: raw.version_count,
  agentKind: raw.agent_kind ?? null,
  source: raw.source ?? null,
  environmentCount: raw.environment_count,
  observabilityEnabled: raw.observability_enabled,
  observeProject: raw.observe_project
    ? { id: raw.observe_project.id, name: raw.observe_project.name }
    : null,
  agentType: raw.agent_type ?? null,
  description: raw.description || null,
  contactNumber: raw.contact_number || null,
  inbound: typeof raw.inbound === "boolean" ? raw.inbound : null,
  targetSpeaksFirst:
    typeof raw.target_speaks_first === "boolean"
      ? raw.target_speaks_first
      : null,
  languages: raw.languages ?? [],
  model: raw.model || raw.model_details?.model || null,
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
  source: raw.source ?? null,
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
        // The version this environment tests by default: its own pin, not
        // whatever its last run happened to use.
        agentVersion: toVersionRef(e.agent_version),
        // Whether it follows the agent's production version or is pinned.
        policy: toPolicy(e.agent_policy),
        lastRun: e.last_run ? toRun(e.last_run) : null,
      })),
    enabled: !!agentId,
  });
}

const invalidateVersions = (queryClient, agentId) =>
  Promise.all([
    queryClient.invalidateQueries({ queryKey: agentDetailQueryKey(agentId) }),
    queryClient.invalidateQueries({
      queryKey: ["agent-definition-versions", agentId],
    }),
    queryClient.invalidateQueries({ queryKey: ["agents", "version", agentId] }),
  ]);

// Makes a version the one in production. Environments keep the version each
// is pinned to; only the marker moves.
export function useActivateVersion(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ versionId }) =>
      axios.post(
        endpoints.agentDefinitions.activateVersion(agentId, versionId),
      ),
    onSuccess: () => invalidateVersions(queryClient, agentId),
  });
}

export function useDeleteAgent(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      axios.delete(endpoints.agentDefinitions.deleteOne(agentId)),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["agents", "connected"] }),
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

// Links a code agent to the Observe project its production traces land in;
// a null projectId clears it. The observability switch is left as it is.
export function useLinkObserveProject(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId }) =>
      axios.put(endpoints.agentDefinitions.edit(agentId), {
        production_project_id: projectId,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: agentDetailQueryKey(agentId) }),
  });
}

// The snippets the Observe setup drawer shows: install, keys, telemetry and
// the instrumentation list, each in Python and TypeScript.
export function useObserveSetupCode() {
  return useQuery({
    queryKey: ["observe-setup-code"],
    queryFn: async () =>
      unwrap(
        (
          await axios.get(endpoints.project.getCodeBlockTracer, {
            params: { project_type: "observe" },
          })
        ).data,
      ),
    staleTime: Infinity,
  });
}

// One change to the agent by kind: `sync` reads the branch head, `upload` a
// new archive, `manual` a prompt edit, `credentials` new values. Answers
// 202 with the new version, or 200 with `up_to_date` and the version that
// already stands for that content.
export function useChangeVersion(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body) =>
      (
        await axios.post(
          endpoints.agentDefinitions.changeVersion(agentId),
          body,
        )
      ).data,
    onSuccess: () => invalidateVersions(queryClient, agentId),
  });
}

// Makes a checked version the one in production; environments that follow
// production move to it, pinned ones are offered it.
export function useSetProduction(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ versionId }) =>
      (
        await axios.post(
          endpoints.agentDefinitions.productionVersion(agentId, versionId),
        )
      ).data,
    onSuccess: () =>
      Promise.all([
        invalidateVersions(queryClient, agentId),
        queryClient.invalidateQueries({
          queryKey: ["agents", "environments", agentId],
        }),
      ]),
  });
}

export function useRetryVersion(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ versionId }) =>
      axios.post(endpoints.agentDefinitions.retryVersion(agentId, versionId)),
    onSuccess: () => invalidateVersions(queryClient, agentId),
  });
}

export function useDiscardVersion(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ versionId }) =>
      axios.post(endpoints.agentDefinitions.discardVersion(agentId, versionId)),
    onSuccess: () => invalidateVersions(queryClient, agentId),
  });
}

// Follow the agent's production version, or pin the environment to one.
export function useSetEnvironmentPolicy(agentId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ environmentId, policy, versionId }) =>
      axios.post(
        endpoints.agentDefinitions.environmentAgentPolicy(environmentId),
        {
          policy,
          ...(versionId ? { version_id: versionId } : {}),
        },
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["agents", "environments", agentId],
      }),
  });
}

// How a version fits the world an environment was built from.
export function useVersionCompatibility(agentId, versionId, environmentId) {
  return useQuery({
    queryKey: ["agents", "compatibility", agentId, versionId, environmentId],
    queryFn: async () =>
      (
        await axios.get(
          endpoints.agentDefinitions.versionCompatibility(agentId, versionId),
          { params: { environment_id: environmentId } },
        )
      ).data,
    enabled: !!agentId && !!versionId && !!environmentId,
  });
}

// What a build from the agent would find: the version, its check, its code.
export function useBuildPreflight(agentId, versionId, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["agents", "build-preflight", agentId, versionId ?? null],
    queryFn: async () =>
      (
        await axios.get(endpoints.agentDefinitions.buildPreflight(agentId), {
          params: versionId ? { version_id: versionId } : {},
        })
      ).data,
    enabled: !!agentId && enabled,
  });
}
