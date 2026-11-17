import { useQuery } from '@tanstack/react-query';
import axios from 'src/utils/axios';
import { RUNTIME_ROUTE_PENDING, runtimeEndpoint } from './endpoints';
import { skillsKeys } from './keys';
import { skillV11Result, useSkillV11Query, useSkillV11Scope } from './testing';

// React Query hooks for the skill-trace runtime reads (SKILL-TRACES §6.6
// endpoints 1–5 and 8–10, §7.8). Every read is scoped by the server to the
// projects the caller can open; the client never widens it.
//
// Response mapping: keys are camelCased, except the values the client must
// pass on unchanged or that are keyed by data:
//   - `links` and `source` stay in the server's wire form, because
//     buildSkillTraceLink hands their §6.3 items to Observe verbatim (UX-02);
//   - `by_error_type`, `requested_labels`, `requested_versions`,
//     `eval_failed`: their keys are error types, labels, versions and eval
//     config ids, not field names.
// Pagination: a page that has more rows carries `next_cursor` (mapped to
// `nextCursor`); pass it back as `cursor` (SKILLS-API-REVIEW: one cursor
// convention). Errors carry `code` (see runtimeErrorKind).

const VERBATIM = new Set(['links', 'source', 'by_error_type', 'requested_labels', 'requested_versions', 'eval_failed']);
const camelKey = (key) => key.replace(/_([a-z0-9])/g, (_, char) => char.toUpperCase());

export function camelizeRuntime(value) {
  if (Array.isArray(value)) return value.map(camelizeRuntime);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    camelKey(key),
    VERBATIM.has(key) ? item : camelizeRuntime(item),
  ]));
}

export const WINDOW_HOURS = Object.freeze({ '24h': 24, '7d': 24 * 7, '30d': 24 * 30 });
const MOVE_WINDOW = /^([1-9]\d*)(h|d)$/;

/** `{from, to}` ISO strings for a tab window, computed when the read runs. */
export function runtimeWindow(windowValue, now = new Date()) {
  const hours = WINDOW_HOURS[windowValue] ?? WINDOW_HOURS['7d'];
  return { from: new Date(now.getTime() - hours * 3600 * 1000).toISOString(), to: now.toISOString() };
}

/** Move-card windows run from 1 hour to 7 days (§6.6 endpoint 3). */
export function normalizeMoveWindow(value) {
  const match = MOVE_WINDOW.exec(String(value || ''));
  if (!match) return '24h';
  const hours = Number(match[1]) * (match[2] === 'd' ? 24 : 1);
  return hours >= 1 && hours <= 168 ? match[0] : '24h';
}

const projectParam = (projectIds) => (Array.isArray(projectIds) && projectIds.length ? projectIds.map(String).join(',') : undefined);
// Blank agent/service values are part of the runtime agent key, not omissions.
const clean = (params) => Object.fromEntries(Object.entries(params).filter(([key, value]) => value !== undefined && value !== null && (value !== '' || key === 'agent' || key === 'service')));

function runtimeRead({ route, path = {}, params = () => ({}), mapper = camelizeRuntime }) {
  return async (config) => {
    const url = runtimeEndpoint(route, path);
    const response = await axios.get(url, { ...config, params: clean(params()) });
    return mapper(skillV11Result(response));
  };
}

/**
 * The kind of failure, for the §7.6 and §7.7 states:
 *   'denied'       no readable project (403 project_not_readable)
 *   'slow'         read budget exceeded ("This took too long. Try a shorter window.")
 *   'unavailable'  runtime data temporarily unavailable, or the route is not served yet
 *   'invalid'      the request was rejected (bad window or ids)
 *   'error'        anything else
 */
export function runtimeErrorKind(error) {
  const code = error?.response?.data?.code || error?.code;
  const status = error?.response?.status;
  if (code === 'project_not_readable' || status === 403) return 'denied';
  if (code === 'runtime_read_budget_exceeded') return 'slow';
  if (code === 'runtime_read_unavailable' || code === 'runtime_read_too_large' || code === RUNTIME_ROUTE_PENDING) return 'unavailable';
  if (code === 'invalid_runtime_query' || status === 400) return 'invalid';
  return 'error';
}

/**
 * Endpoint 1: the skill Runtime tab (Loaded by, summary, diagnosis, failed
 * loads, latest move). Default grouping is version rows that open to agent
 * rows. A row with `agent: null` is the version row; it carries its own
 * cohort numbers, which are never the sum of its agent rows.
 */
export function useSkillRuntime(skillId, { window: windowValue = '7d', projectIds = [], groupBy = 'version,agent', cursor, enabled = true } = {}) {
  const projects = projectParam(projectIds);
  return useSkillV11Query({
    parts: [skillId, 'runtime', { window: windowValue, projects: projects || 'all', groupBy, cursor: cursor || null }],
    load: runtimeRead({
      route: 'skillUsage',
      path: { skill_id: skillId },
      params: () => ({ ...runtimeWindow(windowValue), project_ids: projects, group_by: groupBy, cursor }),
    }),
    enabled: Boolean(skillId) && enabled,
  });
}

/** Endpoint 2: per-version series with label-move markers. */
export function useSkillRuntimeSeries(skillId, { window: windowValue = '7d', projectIds = [], versionIds = [], metric = 'failed_requests', bucket, enabled = true } = {}) {
  const projects = projectParam(projectIds);
  const versions = projectParam(versionIds);
  const step = bucket || (WINDOW_HOURS[windowValue] <= 48 ? 'hour' : 'day');
  return useSkillV11Query({
    parts: [skillId, 'runtimeSeries', { window: windowValue, projects: projects || 'all', versions: versions || 'all', metric, step }],
    load: runtimeRead({
      route: 'skillSeries',
      path: { skill_id: skillId },
      params: () => ({ ...runtimeWindow(windowValue), project_ids: projects, version_ids: versions, metric, bucket: step }),
    }),
    enabled: Boolean(skillId) && enabled,
  });
}

/**
 * Endpoint 3: before/after one production move (the move card and the header
 * chip). Without `eventId` the server answers for the latest move of `label`.
 * `trigger.before` and `trigger.after` are per-agent value lists; the client
 * never averages them.
 */
export function useSkillComparison(skillId, { label = 'production', eventId, window: moveWindow = '24h', enabled = true } = {}) {
  const windowValue = normalizeMoveWindow(moveWindow);
  return useSkillV11Query({
    parts: [skillId, 'runtimeComparison', { label, eventId: eventId || 'latest', window: windowValue }],
    load: runtimeRead({
      route: 'skillComparison',
      path: { skill_id: skillId },
      params: () => ({ label, event_id: eventId, window: windowValue }),
    }),
    enabled: Boolean(skillId) && enabled,
  });
}

/**
 * Endpoint 4: the context Runtime tab, plus `linked_not_loaded`,
 * `loaded_not_linked` and `links_summary` (always the last 7 days).
 */
export function useContextRuntime(contextId, { window: windowValue = '7d', projectIds = [], versionId, enabled = true } = {}) {
  const projects = projectParam(projectIds);
  return useSkillV11Query({
    parts: ['contexts', contextId, 'runtime', { window: windowValue, projects: projects || 'all', versionId: versionId || null }],
    load: runtimeRead({
      route: 'contextUsage',
      path: { context_id: contextId },
      params: () => ({ ...runtimeWindow(windowValue), project_ids: projects, version_id: versionId }),
    }),
    enabled: Boolean(contextId) && enabled,
  });
}

/** Endpoint 5: distinct values for the Observe Skill pickers (lane ST9). */
export function useRuntimeFilterValues({ projectId, field, q, skillId, loadResult, window: windowValue = '7d', enabled = true } = {}) {
  return useSkillV11Query({
    parts: ['runtimeFilterValues', { projectId, field, q: q || '', skillId: skillId || null, loadResult: loadResult || 'loaded', window: windowValue }],
    load: runtimeRead({
      route: 'filterValues',
      params: () => ({ ...runtimeWindow(windowValue), project_id: projectId, field, q, skill_id: skillId, load_result: loadResult }),
    }),
    enabled: Boolean(projectId && field) && enabled,
  });
}

/**
 * Endpoint 8: the agent panel. The agent key is (project, service, agent
 * name); an empty agent name is valid ("Unnamed agent in <project>").
 */
export function useRuntimeAgent({ projectId, service = '', agent = '', window: windowValue = '7d', from, to, enabled = true } = {}) {
  return useSkillV11Query({
    parts: ['runtimeAgent', { projectId, service, agent, window: windowValue, from: from || null, to: to || null }],
    load: runtimeRead({
      route: 'agentUsage',
      params: () => ({ ...(from && to ? { from, to } : runtimeWindow(windowValue)), project_id: projectId, service, agent }),
    }),
    enabled: Boolean(projectId) && enabled,
  });
}

/** Endpoint 9: the context release card, with `changed_skills`. */
export function useContextComparison(contextId, { eventId, window: moveWindow = '24h', enabled = true } = {}) {
  const windowValue = normalizeMoveWindow(moveWindow);
  return useSkillV11Query({
    parts: ['contexts', contextId, 'runtimeComparison', { eventId: eventId || 'latest', window: windowValue }],
    load: runtimeRead({
      route: 'contextComparison',
      path: { context_id: contextId },
      params: () => ({ event_id: eventId, window: windowValue }),
    }),
    enabled: Boolean(contextId) && enabled,
  });
}

export const CHECK_TRACING_POLL_MS = 5000;
export const CHECK_TRACING_LIMIT_MS = 10 * 60 * 1000;

/**
 * Endpoint 10: Check tracing. Polls every 5 s while `active`; the caller stops
 * after 10 minutes. Returns `{load: {version, agent, project, receivedAt} | null,
 * fetch: {sdkVersion, tracingMode, canRecord, client} | null}`.
 */
export function useLatestLoad({ kind = 'skill', id, since, active = false } = {}) {
  const access = useSkillV11Scope();
  const context = kind === 'agent_context';
  return useQuery({
    queryKey: skillsKeys.v11(access.scope, ...(context ? ['contexts', id] : [id]), 'latestLoad', since || null),
    queryFn: async ({ signal }) => {
      const url = runtimeEndpoint(context ? 'contextLatestLoad' : 'skillLatestLoad', context ? { context_id: id } : { skill_id: id });
      const response = await axios.get(url, { params: { since }, signal, headers: access.headers });
      if (signal.aborted || access.queryIdentity.current !== access.identity) throw new Error('The Skills scope changed.');
      return camelizeRuntime(skillV11Result(response)) ?? { load: null, fetch: null };
    },
    enabled: access.enabled && Boolean(id && since) && active,
    retry: false,
    refetchInterval: (query) => (active && !query.state.data?.load ? CHECK_TRACING_POLL_MS : false),
    refetchIntervalInBackground: false,
    meta: { errorHandled: true },
  });
}

/**
 * The Linked skills "Loaded (7 days)" column (UXF5 commit 2 wires it through
 * ContextLinksPanel's `extraColumn`). Reads endpoint 4's `links_summary` and
 * `loaded_not_linked` for the selected context version.
 */
export function useContextLinkLoads(contextId, versionId, { enabled = true } = {}) {
  const query = useContextRuntime(contextId, { window: '7d', versionId, enabled });
  const summary = query.data?.linksSummary ?? [];
  const bySkillId = Object.fromEntries(summary.map((row) => [String(row.skillId), row]));
  return {
    isPending: query.isPending && query.fetchStatus !== 'idle',
    isError: query.isError,
    errorKind: query.isError ? runtimeErrorKind(query.error) : null,
    bySkillId,
    loadedNotLinked: query.data?.loadedNotLinked ?? [],
    refetch: query.refetch,
  };
}
