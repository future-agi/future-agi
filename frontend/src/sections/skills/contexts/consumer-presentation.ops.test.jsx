import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { consumerReportAt } from './consumerPresentation';
import ContextConsumersView from './ContextConsumersView';

afterEach(cleanup);
const row = {
  binding: { id: 'app', name: 'Support app', kind: 'runtime', selector: { label: 'production' } },
  desired: { versionId: 'v2' }, loaded: null, observationAgeSeconds: null,
  states: ['unknown'], observationsComplete: true, nextActionCode: 'observe_runtime',
};
const data = { results: [row], count: 1, page: 1, pageSize: 25, asOf: '2026-11-13T03:00:00Z' };
const renderConsumers = (value = data) => render(<ContextConsumersView scopeKey="workspace" state="ready" data={value} versionNumbers={{ v2: 2 }} onWindowChange={vi.fn()} onPageChange={vi.fn()} />);

it('uses one calm summary and one waiting message for an unreported app', () => {
  renderConsumers();
  expect(screen.getByText('1 app follows this context. It hasn\'t reported which version it received yet.')).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
  const table = screen.getByRole('table', { name: 'Context delivery observations' });
  expect(within(table).getByRole('columnheader', { name: 'Kind' })).toBeVisible();
  expect(within(table).getByText('App')).toBeVisible();
  // Nothing has reported, so the list says so once and each row carries the short tag.
  expect(screen.getByText('No report yet')).toBeVisible();
  expect(within(table).queryByText('No report')).toBeNull();
  expect(within(table).queryByText('No report from this app yet')).toBeNull();
  expect(within(table).getByRole('columnheader', { name: 'App or repository' })).toBeVisible();
  expect(within(table).queryByRole('columnheader', { name: 'Consumer' })).toBeNull();
  expect(within(table).getByText('App · Target v2 · Follow production')).toBeInTheDocument();
  // The report state is said once (the badge), not again in a second line or an all-dash column.
  expect(within(table).queryByText('Waiting for first report')).toBeNull();
  expect(within(table).queryByRole('columnheader', { name: 'Last delivered' })).toBeNull();
  expect(within(table).queryByRole('columnheader', { name: 'Last report' })).toBeNull();
  expect(screen.getByRole('link', { name: 'How apps report' })).toHaveAttribute('href', 'https://docs.futureagi.com/docs/skills/rollout-recovery');
});

it('keeps the long tag when some apps have reported, so rows stay distinguishable', () => {
  const reported = { ...row, binding: { ...row.binding, id: 'live', name: 'Live app' }, states: ['matches_target'], loaded: { versionId: 'v2', digest: 'sha256:x' }, observationAgeSeconds: 60 };
  renderConsumers({ ...data, count: 2, results: [row, reported] });
  expect(screen.queryByText('No app has reported a version yet.')).toBeNull();
  const table = screen.getByRole('table', { name: 'Context delivery observations' });
  expect(within(table).getByText('No report from this app yet')).toBeVisible();
  expect(within(table).queryByText('No report')).toBeNull();
  expect(within(table).getByText('App · Target v2 · Delivered v2 · Follow production')).toBeInTheDocument();
});

it('keeps pin and stale evidence distinct and pluralizes the delivery summary', () => {
  const repository = { ...row, binding: { ...row.binding, id: 'repo', name: 'Support repository', kind: 'repository' }, states: ['pinned_as_intended', 'stale_observation', 'unknown'], observationAgeSeconds: 3600 };
  renderConsumers({ ...data, count: 2, results: [row, repository] });
  expect(screen.getByText('2 apps follow this context. 2 of 2 still need a delivery report to confirm the version received.')).toBeVisible();
  expect(screen.getByText('Pinned as intended')).toBeVisible();
  expect(screen.getByText('Stale observation')).toBeVisible();
  const table = screen.getByRole('table', { name: 'Context delivery observations' });
  expect(table.querySelector('time')).toHaveAttribute('datetime', '2026-11-13T02:00:00.000Z');
  // One consumer has a report, so the Last report column appears; none has a delivery, so Last delivered stays hidden.
  expect(within(table).getByRole('columnheader', { name: 'Last report' })).toBeVisible();
  expect(within(table).queryByRole('columnheader', { name: 'Last delivered' })).toBeNull();
});

it('shows Last delivered once any consumer has a delivered release', () => {
  renderConsumers({ ...data, results: [{ ...row, loaded: { versionId: 'v2', digest: 'sha256:x' } }] });
  expect(within(screen.getByRole('table', { name: 'Context delivery observations' })).getByRole('columnheader', { name: 'Last delivered' })).toBeVisible();
});

it('writes a recent snapshot time in lowercase inside the sentence and keeps the full time in the tooltip', () => {
  const now = Date.now();
  renderConsumers({ ...data, asOf: new Date(now).toISOString() });
  const line = screen.getByText(/^As of/);
  expect(line).toHaveTextContent('As of just now');
  expect(line.querySelector('time')).toHaveAttribute('datetime', new Date(now).toISOString());
});

it.each([null, -1, Infinity, 1e300])('does not invent a report timestamp for age %s', (age) => {
  expect(consumerReportAt({ observationAgeSeconds: age }, data.asOf)).toBeNull();
});
