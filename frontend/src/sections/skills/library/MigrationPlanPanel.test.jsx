import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import MigrationPlanPanel from './MigrationPlanPanel';

const digest = `sha256:${'a'.repeat(64)}`;
const item = { key: 'one', name: 'policy', sourceHubChanges: [], basisPayloadAvailable: false, expectedHeadVersion: null, expectedHeadDigest: null };
const plan = { id: 'plan', fingerprint: digest, expiresAt: '2099-01-01T00:00:00Z', items: [item] };
const choose = (name, option) => { fireEvent.mouseDown(screen.getByRole('combobox', { name })); fireEvent.click(screen.getByRole('option', { name: option, exact: true })); };
const consent = () => {
  choose('Review item', 'policy'); choose('Source update decision', 'Replace with reviewed source');
  fireEvent.click(screen.getByRole('checkbox', { name: /reviewed source rights/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /Apply this exact decision/ }));
};
afterEach(cleanup);

it('labels migration selects without a self-reference to their selected option', () => {
  render(<MigrationPlanPanel scopeKey="scope" plan={plan} onAccept={vi.fn()} />);
  consent();
  for (const name of ['Review item', 'Source update decision', 'File semantics', 'Destination tags']) {
    const select = screen.getByRole('combobox', { name, exact: true });
    const labelIds = select.getAttribute('aria-labelledby').split(' ');
    expect(labelIds).toHaveLength(1);
    expect(document.getElementById(labelIds[0])).toHaveTextContent(name);
    expect(labelIds).not.toContain(select.id);
  }
});

it('requires an explicit supported-subset acknowledgment and never enables a blocked member', async () => {
  const accept = vi.fn(async () => ({ receiptId: 'receipt' }));
  render(<MigrationPlanPanel scopeKey="scope" plan={{ ...plan, rejected: [{ path: 'bad/a.bin', reason: 'file_not_text' }], items: [...plan.items, { key: 'bad', name: 'bad', blocked: true }] }} onAccept={accept} />);
  consent();
  const submit = screen.getByRole('button', { name: 'Import reviewed item as draft' });
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /explicitly exclude/ }));
  expect(submit).toBeEnabled();
  fireEvent.click(submit);
  await waitFor(() => expect(accept).toHaveBeenCalledOnce());
  choose('Review item', 'bad'); choose('Source update decision', 'Replace with reviewed source');
  expect(screen.getByRole('button', { name: 'Import reviewed item as draft' })).toBeDisabled();
});

it('pins the exact body and operation key across a lost import response', async () => {
  const accept = vi.fn().mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce({ receiptId: 'original-receipt' });
  const busy = vi.fn();
  render(<MigrationPlanPanel scopeKey="scope" plan={plan} onAccept={accept} onBusyChange={busy} />);
  consent(); fireEvent.click(screen.getByRole('button', { name: 'Import reviewed item as draft' }));
  await screen.findByText(/The response was lost/);
  expect(screen.getByRole('combobox', { name: 'Review item' })).toHaveAttribute('aria-disabled', 'true');
  expect(screen.getByRole('checkbox', { name: /reviewed source rights/ })).toBeDisabled();
  expect(busy).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByRole('button', { name: 'Retry exact decision' }));
  await screen.findByText(/original-receipt/);
  expect(accept).toHaveBeenCalledTimes(2);
  expect(accept.mock.calls[1]).toEqual(accept.mock.calls[0]);
  await waitFor(() => expect(busy).toHaveBeenLastCalledWith(false));
});

it('allows the next reviewed item after an uncertain decision is confirmed by exact replay', async () => {
  const accept = vi.fn().mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce({ receiptId: 'first' }).mockResolvedValueOnce({ receiptId: 'second' });
  render(<MigrationPlanPanel scopeKey="scope" plan={{ ...plan, items: [item, { ...item, key: 'two', name: 'second-policy' }] }} onAccept={accept} />);
  consent(); fireEvent.click(screen.getByRole('button', { name: 'Import reviewed item as draft' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Retry exact decision' }));
  await screen.findByText(/Receipt first/);
  choose('Review item', 'second-policy'); choose('Source update decision', 'Replace with reviewed source');
  fireEvent.click(screen.getByRole('checkbox', { name: /reviewed source rights/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /Apply this exact decision/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed item as draft' }));
  await screen.findByText(/Receipt second/);
  expect(accept).toHaveBeenCalledTimes(3);
  expect(accept.mock.calls[1]).toEqual(accept.mock.calls[0]);
  expect(accept.mock.calls[2][0].itemKey).toBe('two');
  expect(accept.mock.calls[2][1].idempotencyKey).not.toBe(accept.mock.calls[0][1].idempotencyKey);
});

it('requires the HTTP contract rights acknowledgment for a keep decision too', async () => {
  const keep = vi.fn(async () => ({ receiptId: 'keep-receipt' }));
  render(<MigrationPlanPanel scopeKey="scope" plan={{ ...plan, requiresKeepRights: true }} onKeep={keep} />);
  choose('Review item', 'policy');
  fireEvent.click(screen.getByRole('checkbox', { name: /Apply this exact decision/ }));
  expect(screen.getByRole('button', { name: 'Record keep decision' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /reviewed source rights/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Record keep decision' }));
  await waitFor(() => expect(keep).toHaveBeenCalledOnce());
  expect(keep.mock.calls[0][0].rightsConfirmed).toBe(true);
});

it('blocks replacement when a personal draft exists even if exact comparisons loaded', () => {
  render(<MigrationPlanPanel scopeKey="scope" plan={{ ...plan, items: [{ ...item, hasDraft: true }] }} onAccept={vi.fn()} />);
  consent();
  expect(screen.getByRole('button', { name: 'Import reviewed item as draft' })).toBeDisabled();
  expect(screen.getByText(/Your existing draft blocks replacement/)).toBeVisible();
});
