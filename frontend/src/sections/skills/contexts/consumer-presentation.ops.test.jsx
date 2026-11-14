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
  expect(screen.getByText('1 of 1 connected consumer needs a delivery report to confirm the version received.')).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
  const table = screen.getByRole('table', { name: 'Context delivery observations' });
  expect(within(table).getByRole('columnheader', { name: 'Kind' })).toBeVisible();
  expect(within(table).getByText('App')).toBeVisible();
  expect(within(table).getByText('No report from this app yet')).toBeVisible();
  expect(within(table).getAllByText('Waiting for first report')).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'How apps report' })).toHaveAttribute('href', 'https://docs.futureagi.com/docs/skills/rollout-recovery');
});

it('keeps pin and stale evidence distinct and pluralizes the delivery summary', () => {
  const repository = { ...row, binding: { ...row.binding, id: 'repo', name: 'Support repository', kind: 'repository' }, states: ['pinned_as_intended', 'stale_observation', 'unknown'], observationAgeSeconds: 3600 };
  renderConsumers({ ...data, count: 2, results: [row, repository] });
  expect(screen.getByText('2 of 2 connected consumers need a delivery report to confirm the version received.')).toBeVisible();
  expect(screen.getByText('Pinned as intended')).toBeVisible();
  expect(screen.getByText('Stale observation')).toBeVisible();
  const table = screen.getByRole('table', { name: 'Context delivery observations' });
  expect(table.querySelector('time')).toHaveAttribute('datetime', '2026-11-13T02:00:00.000Z');
});

it.each([null, -1, Infinity, 1e300])('does not invent a report timestamp for age %s', (age) => {
  expect(consumerReportAt({ observationAgeSeconds: age }, data.asOf)).toBeNull();
});
