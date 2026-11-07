import { useEffect, useMemo, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthContext } from 'src/auth/hooks';
import { useOrganization } from 'src/contexts/OrganizationContext';
import { useWorkspace } from 'src/contexts/WorkspaceContext';
import axios from 'src/utils/axios';
import { endpoints } from './endpoints';
import { skillsKeys } from './keys';
import {
  caseInput, inventoryReviewInput, mapAttempt, mapCaseResult, mapEvidence,
  mapRun, mapRunAck, mapRunFile, mapRunPreview, mapSuite, mapTestingOptions,
  mapV11Page, runConfigInput,
} from './v11-mappers';

export const isActiveSkillJob = (status) => ['queued', 'running', 'cancelling'].includes(status);
export const skillJobPollInterval = (query) => isActiveSkillJob(query.state.data?.status) ? 2000 : false;
export const v11PageParams = ({ pageSize, ...params } = {}) => ({ ...params, ...(pageSize === undefined ? {} : { page_size: pageSize }) });
export const skillV11Result = (response) => response.data.result;

// All v1.1 hooks share the feature's one key factory. Explicit request headers
// keep a delayed request from borrowing a newly selected workspace's defaults.
export function useSkillV11Scope() {
  const auth = useAuthContext();
  const organization = useOrganization();
  const workspace = useWorkspace();
  const client = useQueryClient();
  const scope = useMemo(() => ({
    organizationId: organization.currentOrganizationId,
    workspaceId: workspace.currentWorkspaceId,
    userId: auth.authenticated ? auth.user?.id : null,
  }), [organization.currentOrganizationId, workspace.currentWorkspaceId, auth.authenticated, auth.user?.id]);
  const identity = JSON.stringify([scope.organizationId, scope.workspaceId, scope.userId]);
  const current = useRef(identity);
  current.current = identity;
  const previous = useRef({ identity, scope });
  useEffect(() => {
    if (previous.current.identity !== identity) {
      const queryKey = skillsKeys.v11(previous.current.scope);
      client.cancelQueries({ queryKey });
      client.removeQueries({ queryKey });
      previous.current = { identity, scope };
    }
  }, [client, identity, scope]);
  const enabled = Boolean(auth.authenticated && !auth.loading && organization.isReady && workspace.isReady && scope.organizationId && scope.workspaceId && scope.userId);
  return { scope, identity, current, enabled, headers: { 'X-Organization-Id': scope.organizationId, 'X-Workspace-Id': scope.workspaceId } };
}

export function useSkillV11Query({ parts, url, mapper, params, enabled = true, poll = false, terminalParts = [] }) {
  const access = useSkillV11Scope();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: skillsKeys.v11(access.scope, ...parts),
    queryFn: async ({ signal }) => {
      const response = await axios.get(url(), { params, signal, headers: access.headers });
      if (signal.aborted || access.current.current !== access.identity) throw new Error('The Skills scope changed.');
      return mapper(skillV11Result(response));
    },
    enabled: access.enabled && enabled, retry: false,
    refetchInterval: poll ? skillJobPollInterval : false,
    refetchIntervalInBackground: false, meta: { errorHandled: true },
  });
  const settled = useRef(null);
  const terminalKey = JSON.stringify(terminalParts);
  useEffect(() => {
    if (!poll || !query.data || isActiveSkillJob(query.data.status)) return;
    const receipt = `${access.identity}:${query.data.id}:${query.data.status}`;
    if (settled.current === receipt) return;
    settled.current = receipt;
    JSON.parse(terminalKey).forEach((key) => client.invalidateQueries({ queryKey: skillsKeys.v11(access.scope, ...key) }));
  }, [poll, query.data, access.identity, access.scope, client, terminalKey]);
  return query;
}

export function idempotencyHeaders(idempotencyKey) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(idempotencyKey)) {
    throw new Error('Keep a UUID idempotencyKey for this submission, including retries.');
  }
  return { 'Idempotency-Key': idempotencyKey };
}

export function useSkillV11Mutation({ send, invalidate = () => [], invalidateLegacy = () => [] }) {
  const access = useSkillV11Scope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input) => {
      if (!access.enabled || access.current.current !== access.identity) throw new Error('Select an authenticated workspace to continue.');
      const value = await send(input, { headers: access.headers });
      // A completed server job survives navigation. Its late HTTP result must
      // not repopulate a new principal's cache or trigger success UI there.
      if (access.current.current !== access.identity) throw new Error('The Skills scope changed. Reopen the original workspace to inspect the job.');
      return value;
    },
    retry: false, meta: { errorHandled: true },
    onSuccess: (value, input) => {
      if (access.current.current !== access.identity) return;
      invalidate(value, input).forEach((parts) => client.invalidateQueries({ queryKey: skillsKeys.v11(access.scope, ...parts) }));
      invalidateLegacy(value, input).forEach((queryKey) => client.invalidateQueries({ queryKey }));
    },
  });
}

export function useSkillTestingOptions() {
  return useSkillV11Query({ parts: ['testingOptions'], url: endpoints.testingOptions, mapper: mapTestingOptions });
}
export function useSkillTestSuite(skillId) {
  return useSkillV11Query({ parts: [skillId, 'suite'], url: () => endpoints.testSuite(skillId), mapper: mapSuite, enabled: Boolean(skillId) });
}
export function useSaveSkillTestSuite(skillId) {
  return useSkillV11Mutation({
    send: async ({ revision, cases }, config) => mapSuite(skillV11Result(await axios.put(endpoints.testSuite(skillId), { revision, cases: cases.map(caseInput) }, config))),
    invalidate: () => [[skillId, 'suite'], [skillId, 'evidence'], [skillId, 'runs'], [skillId, 'run']],
  });
}
export function assertNoTargetCompetitor(skillId, configuration) {
  if (configuration.competitors.some((competitor) => competitor.skillId === skillId)) throw new Error('The target skill cannot also be a competitor.');
}
export function usePreviewSkillTestRun(skillId) {
  return useSkillV11Mutation({ send: async (configuration, config) => {
    assertNoTargetCompetitor(skillId, configuration);
    return mapRunPreview(skillV11Result(await axios.post(endpoints.testRunPreview(skillId), runConfigInput(configuration), config)));
  } });
}
export function useCreateSkillTestRun(skillId) {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, configuration, expectedFingerprint, acknowledgeProviderProcessing, inventoryReview }, config) => {
      assertNoTargetCompetitor(skillId, configuration);
      return mapRunAck(skillV11Result(await axios.post(endpoints.testRuns(skillId), {
        configuration: runConfigInput(configuration), expected_fingerprint: expectedFingerprint,
        acknowledge_provider_processing: acknowledgeProviderProcessing, inventory_review: inventoryReviewInput(inventoryReview),
      }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } })));
    },
    invalidate: () => [[skillId, 'runs'], [skillId, 'evidence']],
  });
}
export function useSkillTestRuns(skillId, params = {}) {
  return useSkillV11Query({ parts: [skillId, 'runs', params], url: () => endpoints.testRuns(skillId), params: v11PageParams(params), mapper: (value) => mapV11Page(value, mapRun), enabled: Boolean(skillId) });
}
export function useSkillTestRun(skillId, runId) {
  return useSkillV11Query({ parts: [skillId, 'run', runId], url: () => endpoints.testRun(skillId, runId), mapper: mapRun, enabled: Boolean(skillId && runId), poll: true, terminalParts: [[skillId, 'results', runId], [skillId, 'attempt', runId], [skillId, 'runs'], [skillId, 'evidence']] });
}
export function useSkillTestResults(skillId, runId, params = {}) {
  return useSkillV11Query({ parts: [skillId, 'results', runId, params], url: () => endpoints.testResults(skillId, runId), params: v11PageParams(params), mapper: (value) => mapV11Page(value, mapCaseResult), enabled: Boolean(skillId && runId) });
}
export function useSkillTestAttempt(skillId, runId, attemptId) {
  return useSkillV11Query({ parts: [skillId, 'attempt', runId, attemptId], url: () => endpoints.testAttempt(skillId, runId, attemptId), mapper: mapAttempt, enabled: Boolean(skillId && runId && attemptId) });
}
export function useSkillTestFile(skillId, runId, { snapshotSkillId, variant, path, offset = 0, maxBytes } = {}) {
  return useSkillV11Query({
    parts: [skillId, 'runFile', runId, snapshotSkillId, variant, path, offset, maxBytes],
    url: () => endpoints.testFiles(skillId, runId), mapper: mapRunFile,
    params: { snapshot_skill_id: snapshotSkillId, variant, path, offset, ...(maxBytes === undefined ? {} : { max_bytes: maxBytes }) },
    enabled: Boolean(skillId && runId && snapshotSkillId && variant && path),
  });
}
export function useCancelSkillTestRun(skillId, runId) {
  return useSkillV11Mutation({ send: async (_, config) => skillV11Result(await axios.post(endpoints.cancelTestRun(skillId, runId), {}, config)), invalidate: () => [[skillId, 'run', runId], [skillId, 'runs']] });
}
export function useShareSkillTestRun(skillId, runId) {
  return useSkillV11Mutation({
    send: async ({ acknowledgeSnapshotSharing }, config) => {
      const value = skillV11Result(await axios.post(endpoints.shareTestRun(skillId, runId), { acknowledge_snapshot_sharing: acknowledgeSnapshotSharing }, config));
      return { id: value.id, visibility: value.visibility, sharedAt: value.shared_at };
    },
    invalidate: () => [[skillId, 'run', runId], [skillId, 'runs'], [skillId, 'evidence']],
  });
}
export function useSkillTestEvidence(skillId, candidate) {
  return useSkillV11Query({
    parts: [skillId, 'evidence', candidate], url: () => endpoints.testEvidence(skillId), mapper: mapEvidence,
    params: candidate?.kind === 'draft' ? { candidate: 'draft', revision: candidate.revision } : { candidate: 'version', version: candidate?.version },
    enabled: Boolean(skillId && candidate),
  });
}
