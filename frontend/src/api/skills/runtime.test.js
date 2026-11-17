import { createElement } from 'react';
import PropTypes from 'prop-types';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import axios from 'src/utils/axios';
import {
  camelizeRuntime, normalizeMoveWindow, runtimeErrorKind, runtimeWindow,
  useContextLinkLoads, useLatestLoad, useSkillComparison, useSkillRuntime, useRuntimeAgent,
} from './runtime';

const routes = vi.hoisted(() => ({ pending: false }));
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn() } }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, user: { id: 'user-a' } }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ currentOrganizationId: 'org-a', isReady: true }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ currentWorkspaceId: 'ws-a', isReady: true }) }));
vi.mock('./endpoints', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    runtimeEndpoint: (name, params = {}) => {
      if (routes.pending) {
        const error = new Error('pending');
        error.code = actual.RUNTIME_ROUTE_PENDING;
        throw error;
      }
      return `/runtime/${name}/${params.skill_id || params.context_id || ''}`;
    },
  };
});

const headers = { 'X-Organization-Id': 'org-a', 'X-Workspace-Id': 'ws-a' };
const items = [{ column_id: 'skill.version_id', filter_config: { col_type: 'SKILL', filter_type: 'text', filter_op: 'equals', filter_value: 'v-id' } }];
const wire = {
  coverage: { projects_scanned: 2, projects_with_loads: 1, unreadable_projects: 1, instrumented: true },
  failed_loads: { total: 3, by_error_type: { not_found: 1, integrity: 2 }, links: { 'project-a': { all: { project_id: 'project-a', filters: items } } } },
  rows: [{ version_id: 'v-id', requested_labels: { production: 4 }, requested_versions: { 3: 1 }, failed_requests: { count: 1, rate: 0.1 }, links: { project_id: 'project-a', all: { project_id: 'project-a', filters: items }, eval_failed: { 'config_a': { filters: items } } } }],
  next_cursor: 'cursor-2',
};
const wrapper = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  function QueryWrapper({ children }) { return createElement(QueryClientProvider, { client }, children); }
  QueryWrapper.propTypes = { children: PropTypes.node };
  return QueryWrapper;
};

beforeEach(() => {
  vi.clearAllMocks();
  routes.pending = false;
  axios.get.mockResolvedValue({ data: { result: wire } });
});

describe('runtime response mapping', () => {
  it('camelCases fields but keeps link items, sources and data-keyed maps verbatim', () => {
    const value = camelizeRuntime(wire);
    expect(value.coverage).toEqual({ projectsScanned: 2, projectsWithLoads: 1, unreadableProjects: 1, instrumented: true });
    expect(value.failedLoads.byErrorType).toEqual({ not_found: 1, integrity: 2 });
    expect(value.failedLoads.links['project-a'].all.filters).toEqual(items);
    expect(value.rows[0].links.project_id).toBe('project-a');
    expect(value.rows[0].links.eval_failed.config_a.filters).toEqual(items);
    expect(value.rows[0].requestedLabels).toEqual({ production: 4 });
    expect(value.rows[0].failedRequests).toEqual({ count: 1, rate: 0.1 });
    expect(value.nextCursor).toBe('cursor-2');
  });

  it.each([
    [{ response: { status: 403, data: { code: 'project_not_readable' } } }, 'denied'],
    [{ response: { status: 503, data: { code: 'runtime_read_budget_exceeded' } } }, 'slow'],
    [{ response: { status: 503, data: { code: 'runtime_read_unavailable' } } }, 'unavailable'],
    [{ code: 'runtime_route_pending' }, 'unavailable'],
    [{ response: { status: 400, data: { code: 'invalid_runtime_query' } } }, 'invalid'],
    [new Error('network'), 'error'],
  ])('maps %j to the %s state', (error, kind) => {
    expect(runtimeErrorKind(error)).toBe(kind);
  });

  it('computes windows at read time and bounds the move window to 1 hour–7 days', () => {
    const now = new Date('2026-11-17T12:00:00Z');
    expect(runtimeWindow('24h', now)).toEqual({ from: '2026-11-16T12:00:00.000Z', to: '2026-11-17T12:00:00.000Z' });
    expect(runtimeWindow('nonsense', now).from).toBe('2026-11-10T12:00:00.000Z');
    expect(normalizeMoveWindow('6h')).toBe('6h');
    expect(normalizeMoveWindow('7d')).toBe('7d');
    expect(normalizeMoveWindow('8d')).toBe('24h');
    expect(normalizeMoveWindow('0h')).toBe('24h');
    expect(normalizeMoveWindow('1w')).toBe('24h');
  });
});

describe('runtime hooks', () => {
  it('reads the skill runtime with explicit tenant headers, window, projects and grouping', async () => {
    const { result } = renderHook(() => useSkillRuntime('skill-a', { window: '24h', projectIds: ['p1', 'p2'] }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const [url, config] = axios.get.mock.calls[0];
    expect(url).toBe('/runtime/skillUsage/skill-a');
    expect(config.headers).toEqual(headers);
    expect(config.params).toMatchObject({ project_ids: 'p1,p2', group_by: 'version,agent' });
    expect(new Date(config.params.to) - new Date(config.params.from)).toBe(24 * 3600 * 1000);
    expect(result.current.data.rows[0].versionId).toBe('v-id');
  });

  it('omits project_ids for "every project you can open"', async () => {
    const { result } = renderHook(() => useSkillRuntime('skill-a'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get.mock.calls[0][1].params).not.toHaveProperty('project_ids');
  });

  it('fails with a typed code, and calls nothing, while the route is not served', async () => {
    routes.pending = true;
    const { result } = renderHook(() => useSkillRuntime('skill-a'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(runtimeErrorKind(result.current.error)).toBe('unavailable');
    expect(axios.get).not.toHaveBeenCalled();
  });

  it('keeps empty agent and service identities instead of widening the query', async () => {
    const { result } = renderHook(() => useRuntimeAgent({ projectId: 'p1', agent: '', service: '' }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get.mock.calls[0][1].params).toMatchObject({ project_id: 'p1', agent: '', service: '' });
  });

  it('asks for one move by event id and window', async () => {
    const { result } = renderHook(() => useSkillComparison('skill-a', { eventId: 'event-a', window: '6h' }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get.mock.calls[0][1].params).toEqual({ label: 'production', event_id: 'event-a', window: '6h' });
  });

  it('indexes the 7-day link summary by skill id', async () => {
    axios.get.mockResolvedValue({ data: { result: { links_summary: [{ skill_id: 's1', linked_version: 2, traces_7d: 12, loaded_versions: [2] }], loaded_not_linked: [{ skill_id: 's9', name: 'extra', version: 1, traces: 3 }] } } });
    const { result } = renderHook(() => useContextLinkLoads('ctx-a', 'ctx-v2'), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isPending).toBe(false));
    expect(result.current.bySkillId.s1).toEqual({ skillId: 's1', linkedVersion: 2, traces7d: 12, loadedVersions: [2] });
    expect(result.current.loadedNotLinked).toHaveLength(1);
    expect(axios.get.mock.calls[0][1].params).toMatchObject({ version_id: 'ctx-v2' });
  });

  it('does not read the latest load until Check tracing starts', async () => {
    const { rerender, result } = renderHook((props) => useLatestLoad(props), { wrapper: wrapper(), initialProps: { id: 'skill-a', since: '2026-11-17T12:00:00Z', active: false } });
    expect(axios.get).not.toHaveBeenCalled();
    axios.get.mockResolvedValue({ data: { result: { load: null, fetch: { sdk_version: '0.9.2', tracing_mode: 'on', can_record: false } } } });
    rerender({ id: 'skill-a', since: '2026-11-17T12:00:00Z', active: true });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith('/runtime/skillLatestLoad/skill-a', expect.objectContaining({ params: { since: '2026-11-17T12:00:00Z' }, headers }));
    expect(result.current.data.fetch).toEqual({ sdkVersion: '0.9.2', tracingMode: 'on', canRecord: false });
  });
});
