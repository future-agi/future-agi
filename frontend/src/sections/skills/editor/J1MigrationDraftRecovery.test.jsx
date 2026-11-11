import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import J1MigrationDraftRecovery from './J1MigrationDraftRecovery';
import { mapDraft } from 'src/api/skills/mappers';

const api = vi.hoisted(() => ({ checkpoint: vi.fn() }));
vi.mock('src/api/skills/r3-intake', () => ({ useCheckpointDraft: () => ({ mutateAsync: api.checkpoint }) }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'org:workspace:owner' }) }));
const recoveryDigest = `sha256:${'b'.repeat(64)}`;
const saved = { revision: 4, recoveryDigest, baseVersion: 2, files: [{ path: 'SKILL.md', content: 'Private draft' }], note: '', editSequence: 0 };
const skill = { id: 'leaf', name: 'private-policy' };
const preserve = () => {
  fireEvent.click(screen.getByRole('checkbox', { name: 'Save a recovery checkpoint and suspend my current draft.' }));
  fireEvent.click(screen.getByRole('button', { name: 'Preserve and suspend draft' }));
};
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it('keeps the server recovery identity distinct from any content digest', () => {
  expect(mapDraft({ files: [], revision: 4, recovery_digest: recoveryDigest, digest: `sha256:${'a'.repeat(64)}` }).recoveryDigest).toBe(recoveryDigest);
});

it('flushes and explicitly suspends the exact saved leaf draft without publication or deletion', async () => {
  const flush = vi.fn(async () => saved);
  const onPreserved = vi.fn();
  api.checkpoint.mockResolvedValue({ suspended: true, checkpoint: { id: 'private-checkpoint' } });
  render(<J1MigrationDraftRecovery skill={skill} session={{ ...saved, flush }} onPreserved={onPreserved} />);
  expect(screen.getByRole('button', { name: 'Preserve and suspend draft' })).toBeDisabled();
  preserve();
  await waitFor(() => expect(onPreserved).toHaveBeenCalledOnce());
  expect(flush).toHaveBeenCalledOnce();
  expect(api.checkpoint).toHaveBeenCalledWith({ revision: 4, expectedDigest: recoveryDigest, reason: 'switch', suspend: true, idempotencyKey: expect.any(String) });
});

it('recovers a lost suspension response using the original body/key without a second save', async () => {
  const flush = vi.fn(async () => saved);
  const onPreserved = vi.fn();
  api.checkpoint.mockRejectedValueOnce(new Error('lost response')).mockResolvedValueOnce({ suspended: true, checkpoint: { id: 'private-checkpoint' } });
  render(<J1MigrationDraftRecovery skill={skill} session={{ ...saved, flush }} onPreserved={onPreserved} />);
  preserve();
  await screen.findByText(/The operation could not be confirmed/);
  expect(onPreserved).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Preserve and suspend draft' }));
  await waitFor(() => expect(onPreserved).toHaveBeenCalledOnce());
  expect(api.checkpoint.mock.calls[1]).toEqual(api.checkpoint.mock.calls[0]);
  expect(flush).toHaveBeenCalledOnce();
});

it('does not suspend when the save conflicts or the acknowledged recovery digest is missing', async () => {
  const flush = vi.fn(async () => null);
  render(<J1MigrationDraftRecovery skill={skill} session={{ ...saved, flush }} onPreserved={vi.fn()} />);
  preserve();
  await screen.findByText(/The reviewed state changed/);
  expect(api.checkpoint).not.toHaveBeenCalled();
  flush.mockResolvedValue({ ...saved, recoveryDigest: undefined });
  fireEvent.click(screen.getByRole('button', { name: 'Preserve and suspend draft' }));
  await screen.findByText(/The operation could not be confirmed/);
  expect(api.checkpoint).not.toHaveBeenCalled();
});
