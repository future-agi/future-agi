// Test-only fixtures and render helper for the runtime component tests.
// Shapes follow SKILL-TRACES §6.6 after runtime.js camelCasing; `links` keep
// the server's wire form.
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { dsPalette, dsTypography } from 'src/theme/ds-tokens';

export const SKILL = { id: 'skill-a', name: 'refund-policy' };
export const V3 = '33333333-3333-4333-8333-333333333333';
export const V4 = '44444444-4444-4444-8444-444444444444';
export const NOW = Date.parse('2026-11-17T12:00:00Z');

const item = (column, value) => ({ column_id: column, filter_config: { col_type: 'SKILL', filter_type: 'text', filter_op: 'equals', filter_value: value } });
export const link = (projectId, versionId, extra = []) => ({
  project_id: projectId,
  from: '2026-11-10T12:00:00Z',
  to: '2026-11-17T12:00:00Z',
  filters: [item('skill.version_id', versionId), ...extra],
  source: { skill_id: SKILL.id, version_id: versionId, agent_key: null, metric: 'all', event_id: null, side: null },
});
export const links = (projectId, versionId) => ({
  project_id: projectId,
  all: link(projectId, versionId),
  failed: link(projectId, versionId, [item('skill.root_status', 'error')]),
  slowest: { ...link(projectId, versionId), sort: { column_id: 'latency', direction: 'desc' } },
  eval_failed: { 'eval-1': link(projectId, versionId, [item('eval.eval-1', 'fail')]) },
});

const metrics = (traces, failed, rate, extra = {}) => ({
  traces,
  loads: traces,
  incomplete: 0,
  failedRequests: { count: failed, rate },
  errorsAnySpan: failed,
  latencyMs: { p50: 820, p95: 2400 },
  cost: { perTraceMean: 0.0123, perTraceP50: 0.01, total: traces * 0.0123 },
  tokens: { perTraceMean: 1520.4, total: traces * 1520 },
  inScopeCost: null,
  loadOverheadMsP50: 12,
  cachedShare: 0.4,
  evals: [{ configId: 'eval-1', name: 'Groundedness', mean: 0.82, passRate: 0.9, evaluated: Math.round(traces * 0.4), coverage: 0.4 }],
  lowSample: traces < 30,
  firstSeen: '2026-11-10T12:00:00Z',
  lastSeen: '2026-11-17T11:00:00Z',
  requestedLabels: { production: Math.round(traces * 0.96) },
  requestedVersions: { 3: Math.round(traces * 0.04) },
  ...extra,
});

export const agent = (name, projectId, projectName, extra = {}) => ({
  key: [projectId, `${name}-svc`, name],
  name: name || `${name}-svc`,
  service: `${name}-svc`,
  projectId,
  projectName,
  context: null,
  bindingId: null,
  nameSource: name ? 'agent' : 'service',
  ...extra,
});

export const ROWS = [
  { versionId: V4, version: 4, labelsNow: ['production'], agent: null, digestStatus: 'verified', ...metrics(1204, 31, 0.0257),
    links: { ...links(null, V4), project_id: null },
    projects: [
      { projectId: 'p1', projectName: 'acme-support-production', traces: 1000, failedRequests: { count: 30, rate: 0.03 } },
      { projectId: 'p2', projectName: 'acme-sales', traces: 204, failedRequests: { count: 1, rate: 0.005 } },
    ] },
  { versionId: V4, version: 4, labelsNow: ['production'], agent: agent('refund-agent', 'p1', 'acme-support-production'), ...metrics(1000, 30, 0.03), links: links('p1', V4) },
  { versionId: V4, version: 4, labelsNow: ['production'], agent: agent('', 'p2', 'acme-sales', { name: 'Unnamed agent in acme-sales', nameSource: 'project', service: '' }), ...metrics(12, 2, 0.1667), links: links('p2', V4) },
  { versionId: V3, version: 3, labelsNow: [], agent: null, ...metrics(400, 3, 0.0075), links: links('p1', V3) },
  { versionId: V3, version: 3, labelsNow: [], agent: agent('billing-agent', 'p1', 'acme-support-production'), ...metrics(400, 3, 0.0075), links: links('p1', V3) },
];

export const RUNTIME = {
  coverage: { projectsScanned: 2, projectsWithLoads: 2, unreadableProjects: 1, instrumented: true },
  summary: {
    agents: 3, projects: 2, traces: 1604, failedRequests: { count: 34, rate: 0.021 }, latencyMs: { p95: 2400 }, cost: { perTraceMean: 0.0123 },
    sameAgentCompare: { versionA: 4, versionB: 3, agents: 1, metric: 'failed_requests', a: 0.021, b: 0.008 },
  },
  diagnosis: { state: 'recording', consumers: [] },
  failedLoads: { total: 14, byErrorType: { integrity: 12, authorization: 2 }, links: { all: link('p1', V4, [item('skill.load_result', 'failed')]) } },
  move: null,
  rows: ROWS,
  mismatch: { loads: 31, traces: 20, links: { all: link('p1', V4) } },
  totalsNote: 'cohorts overlap; not additive',
  truncated: false,
};

export const MOVE = {
  eventId: 'event-1',
  label: 'production',
  fromVersion: 3,
  toVersion: 4,
  fromVersionId: V3,
  toVersionId: V4,
  movedAt: '2026-11-17T06:00:00Z',
  endsAt: '2026-11-18T06:00:00Z',
  complete: false,
  beforeCutAt: null,
  verdict: 'worse_so_far',
  trigger: { metric: 'failed_requests', agents: 5, before: [{ agentKey: ['p1', 's', 'a'], value: 0.008 }], after: [{ agentKey: ['p1', 's', 'a'], value: 0.021 }] },
  soFar: true,
  collecting: null,
  sameAgents: {
    agents: 5,
    metrics: [
      { metric: 'latency_p95', before: 2100, after: 2200, delta: 0.05, lowSample: false, beforeTraces: 300, afterTraces: 212 },
      { metric: 'failed_requests', before: 0.008, after: 0.021, delta: 0.013, lowSample: false, beforeTraces: 300, afterTraces: 212, beforeCount: 2, afterCount: 31 },
    ],
  },
  control: { version: 3, agents: 2, metrics: [{ metric: 'failed_requests', before: 0.01, after: 0.009, delta: null, lowSample: true, beforeTraces: 20, afterTraces: 12, beforeCount: 0, afterCount: 1 }] },
  allAgents: { metrics: [{ metric: 'failed_requests', before: 0.008, after: 0.02, delta: 0.012, lowSample: false, beforeTraces: 320, afterTraces: 240, beforeCount: 3, afterCount: 31 }] },
  byAgent: [{ agent: agent('refund-agent', 'p1', 'acme-support-production'), metrics: [{ metric: 'failed_requests', before: 0.008, after: 0.03, delta: 0.022, lowSample: false, beforeTraces: 100, afterTraces: 90 }] }],
  adoption: [
    { agent: agent('a-new', 'p1', 'x'), share: 1, notMovedReason: 'got_new', cached: false },
    { agent: agent('a-version', 'p1', 'x'), share: 0, notMovedReason: 'asked_for_version', cached: false },
    { agent: agent('a-pinned', 'p1', 'x'), share: 0, notMovedReason: 'pinned_in_context', cached: false, context: { id: 'ctx-1', name: 'support-agent' } },
    { agent: agent('a-waiting', 'p1', 'x'), share: 0, notMovedReason: 'waiting_for_context_release', cached: false, context: { id: 'ctx-1', name: 'support-agent' } },
    { agent: agent('a-old', 'p1', 'x'), share: 0.2, notMovedReason: 'got_old_after_move', cached: true },
    { agent: agent('a-unseen', 'p1', 'x'), share: null, notMovedReason: 'not_seen', cached: false },
  ],
  links: {
    before: { failed_requests: link('p1', V3, [item('skill.root_status', 'error')]) },
    after: { failed_requests: link('p1', V4, [item('skill.root_status', 'error')]) },
  },
  primaryAction: 'review_failed',
};

export const success = (data, extra = {}) => ({ data, isPending: false, isError: false, isSuccess: true, fetchStatus: 'idle', error: null, refetch: () => Promise.resolve(), ...extra });
export const pending = () => ({ data: undefined, isPending: true, isError: false, isSuccess: false, fetchStatus: 'fetching', error: null, refetch: () => Promise.resolve() });
export const failure = (code, status = 503) => ({ data: undefined, isPending: false, isError: true, isSuccess: false, fetchStatus: 'idle', error: { response: { status, data: { code } } }, refetch: () => Promise.resolve() });

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location" hidden>{`${location.pathname}${location.search}`}</div>;
}

/** Render inside a router and the design-system theme (light or dark). `update(ui)` re-renders in place. */
export function renderRuntime(ui, { route = '/dashboard/skills/skill-a?tab=runtime', mode = 'light' } = {}) {
  const theme = createTheme({ palette: dsPalette(mode), typography: dsTypography });
  const wrap = (node) => (
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="*" element={<>{node}<LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
  const result = render(wrap(ui));
  return { ...result, update: (next) => result.rerender(wrap(next)) };
}
