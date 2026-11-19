import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ContextDraftSwitch from './ContextDraftSwitch';

const recoveryDigest = `sha256:${'a'.repeat(64)}`;
const draft = { revision: 12, baseVersion: 6, recoveryDigest, files: [{ path: 'AGENTS.md', content: 'Unrelated personal work' }], note: 'Keep this note', contextSpec: {}, resolvedLock: { links: [] } };
const checkpoint = { checkpoint: { id: 'retained-draft', expiresAt: '2099-01-01T00:00:00Z' }, suspended: true, draftRevision: 0, retainedRevision: 13 };
const start = () => ({ revision: 14, baseVersion: 4, files: [{ path: 'AGENTS.md', content: 'Historical v4 policy' }] });
function setup(overrides = {}) {
  const props = { scopeKey: 'organization:workspace:author:context', draft, targetVersion: 4, onSuspend: vi.fn(async () => checkpoint), onStart: vi.fn(async () => start()), onStarted: vi.fn(), ...overrides };
  render(<ContextDraftSwitch {...props} />);
  return props;
}
const preserve = () => {
  fireEvent.click(screen.getByRole('checkbox', { name: 'Save a recovery checkpoint and suspend my current draft.' }));
  fireEvent.click(screen.getByRole('button', { name: 'Preserve and suspend draft' }));
};
afterEach(cleanup);

it('requires an explicit checkpoint and then starts from v4 once, never from latest v6', async () => {
  const props = setup();
  expect(screen.getByRole('button', { name: 'Preserve and suspend draft' })).toBeDisabled();
  expect(props.onSuspend).not.toHaveBeenCalled();
  expect(props.onStart).not.toHaveBeenCalled();
  preserve();
  await waitFor(() => expect(props.onStarted).toHaveBeenCalledWith(start()));
  expect(props.onSuspend.mock.calls[0][0]).toEqual({ revision: 12, expectedDigest: recoveryDigest, reason: 'switch' });
  expect(props.onStart).toHaveBeenCalledOnce();
  expect(props.onStart.mock.calls[0][0]).toEqual({ revision: 0, fromVersion: 4 });
  expect(screen.getByText(/Checkpoint retained-draft/)).toBeVisible();
});

it('retries the same suspension after a lost response without starting another draft', async () => {
  const onSuspend = vi.fn().mockRejectedValueOnce(new Error('Response lost')).mockResolvedValueOnce(checkpoint);
  const props = setup({ onSuspend });
  preserve();
  await screen.findByText(/operation could not be confirmed/);
  expect(props.onStart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Preserve and suspend draft' }));
  await waitFor(() => expect(props.onStarted).toHaveBeenCalledOnce());
  expect(onSuspend.mock.calls[1]).toEqual(onSuspend.mock.calls[0]);
  expect(props.onStart).toHaveBeenCalledOnce();
});

it('retains the checkpoint after a conflicting start and offers a retry instead of suspending again', async () => {
  const props = setup({ onStart: vi.fn().mockRejectedValueOnce({ statusCode: 409, code: 'draft_conflict' }).mockResolvedValue(start()) });
  preserve();
  await screen.findByText(/reviewed state changed/);
  expect(screen.getByText(/Checkpoint retained-draft/)).toBeVisible();
  expect(props.onStarted).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Start preserved-origin draft' }));
  await waitFor(() => expect(props.onStarted).toHaveBeenCalledWith(start()));
  expect(props.onSuspend).toHaveBeenCalledOnce();
});
