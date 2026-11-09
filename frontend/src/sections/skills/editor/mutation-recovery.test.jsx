import PropTypes from 'prop-types';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import useDraftSession from './useDraftSession';
import HotfixHeadReview from './HotfixHeadReview';
import ContextLinksPanel from '../contexts/ContextLinksPanel';

const api = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock('src/api/skills', () => ({ useSaveSkillDraft: () => ({ mutateAsync: api.save }), getSkillErrorCode: (error) => error.code }));
const initial = { revision: 4, baseVersion: 2, files: [{ path: 'AGENTS.md', content: 'Rules' }], description: 'Purpose', note: '', contextSpec: { links: [] }, resolvedLock: { links: [] } };
const resourceApi = { saveDraft: api.save, saveInput: (draft, revision) => ({ ...draft, revision }) };
let session;
function Harness({ head, send }) {
  session = useDraftSession('context', initial, true, resourceApi);
  return head ? <HotfixHeadReview scopeKey="org:workspace:user:context" session={session}
    onPreview={async () => ({ review: { draftRevision: 4, baseVersion: 2, candidateDigest: 'digest', expectedHeadVersion: 3, reviewFingerprint: 'head' }, originChanges: [], headChanges: [] })}
    onAcknowledge={send} /> : <ContextLinksPanel scopeKey="org:workspace:user:context" session={session}
      onPreview={async () => ({ revision: 4, candidateDigest: 'digest', fingerprint: 'links', changes: [], checks: { summary: { errors: 0 } } })} onApply={send} />;
}
Harness.propTypes = { head: PropTypes.bool, send: PropTypes.func.isRequired };
beforeEach(() => { api.save.mockReset(); api.save.mockImplementation(async (input) => ({ ...input, revision: input.revision + 1 })); });
afterEach(cleanup);
it.each([false, true])('recovers a real queued mutation through its visible control (head=%s)', async (head) => {
  const send = vi.fn().mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({ ...initial, revision: 5 });
  render(<Harness head={head} send={send} />);
  const previewLabel = head ? 'Review against current latest' : 'Preview dependency changes';
  const applyLabel = head ? 'Acknowledge reviewed head' : 'Apply selected updates to draft';
  fireEvent.click(screen.getByRole('button', { name: previewLabel }));
  const confirmation = await screen.findByRole('checkbox', { name: head ? 'I reviewed both comparisons and the new-latest consequence.' : 'Accept these exact resolved versions and selector policies.' });
  fireEvent.click(confirmation);
  fireEvent.click(screen.getByRole('button', { name: applyLabel }));
  const retry = await screen.findByRole('button', { name: head ? 'Retry head acknowledgment' : 'Retry dependency update' });
  expect(retry).toBeEnabled();
  expect(session.mutationError).toBeTruthy();
  expect(screen.getByRole('button', { name: previewLabel })).toBeDisabled();
  expect(screen.getByRole('button', { name: applyLabel })).toBeDisabled();
  await act(async () => { session.editFields({ note: 'Local edit after unknown result' }); expect(await session.save()).toBeNull(); expect(await session.flush()).toBeNull(); });
  expect(api.save).not.toHaveBeenCalled();
  fireEvent.click(retry);
  await waitFor(() => expect(session.mutationError).toBeNull());
  expect(send).toHaveBeenCalledTimes(2);
  expect(send.mock.calls[1]).toEqual(send.mock.calls[0]);
  expect(session.revision).toBe(5);
  expect(session.note).toBe('Local edit after unknown result');
  await act(async () => { await session.flush(); });
  expect(api.save).toHaveBeenCalledWith(expect.objectContaining({ revision: 5, note: 'Local edit after unknown result' }));
  expect(session.dirty).toBe(false);
});
