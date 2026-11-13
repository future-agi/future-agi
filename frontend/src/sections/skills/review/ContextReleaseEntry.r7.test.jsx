import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ContextLabelRequestPanel from '../contexts/ContextLabelRequestPanel';
import ReleaseRequestPanel from './ReleaseRequestPanel';
import { suggestedEvidence } from './evidenceSuggestions';

const navigated = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async (load) => ({ ...(await load()), useNavigate: () => navigated }));
vi.mock('src/api/skills/context-authoring', () => ({ useContextDetail: () => ({ data: { context: { name: 'Support agent' }, releaseState: { labels: [{ name: 'production', version: 1 }] } } }) }));
afterEach(() => { cleanup(); localStorage.clear(); });
const packet = { subjectId: 'context', subjectKind: 'agent_context', candidateDigest: 'digest', candidateVersionId: 'candidate', evidence: [], target: null,
  transition: { label: 'production', expectedVersion: 1, version: 2 } };
const ref = { source: 'skills_text', sourceId: 'run', subjectId: 'context', subjectKind: 'agent_context', candidateDigest: 'digest', visibility: 'workspace', status: 'completed', payloadExpiresAt: '2099-01-01', configuration: { text: {} }, annotationRevision: 7 };

it('hands the exact context to the canonical composer without a UUID field or mutation', () => {
  render(<MemoryRouter><ContextLabelRequestPanel contextId="context" version={{ id: 'candidate', number: 2, digest: 'digest' }} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Request release review' }));
  const url = new URL(navigated.mock.calls.at(-1)[0], 'https://example.invalid');
  expect(Object.fromEntries(url.searchParams)).toMatchObject({ compose: '1', subject: 'context', name: 'Support agent', candidate: 'candidate', number: '2', digest: 'digest', label: 'production' });
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByText(/An independent reviewer approves/)).toBeVisible();
});
it('does not carry a stored reviewer across logins or auto-submit for a sole eligible reviewer', () => {
  localStorage.setItem('skills.releaseRequest.lastReviewer:other-login', 'someone-else');
  const submit = vi.fn();
  const props = { scopeKey: 'new-login', state: 'ready', preview: { packet, fingerprint: 'fp', current: true }, review: { complete: true, dependencyChanges: [] }, onSubmit: submit };
  const { rerender } = render(<ReleaseRequestPanel {...props} recipients={[{ id: 'eligible', name: 'Independent reviewer' }]} permissions={{ canSubmit: true }} />);
  expect(screen.getByRole('combobox', { name: 'Assigned reviewer' })).toHaveTextContent('Independent reviewer');
  expect(screen.getByRole('button', { name: 'Send for review' })).toBeDisabled();
  expect(submit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('button', { name: 'Send for review' })).toBeEnabled();
  rerender(<ReleaseRequestPanel {...props} scopeKey="another-login" recipients={[]} permissions={{ canSubmit: true }} />);
  expect(screen.getByRole('button', { name: 'Send for review' })).toBeDisabled();
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});
it('resolves carried refs against fresh options, never URL annotation revisions', () => {
  expect(suggestedEvidence([ref], packet, ['skills_text:run'])).toEqual([{ source: 'skills_text', sourceId: 'run', annotationRevision: 7 }]);
  expect(suggestedEvidence([{ ...ref, visibility: 'private' }], packet, ['skills_text:run'])).toEqual([]);
  expect(suggestedEvidence([{ ...ref, candidateDigest: 'different' }], packet, ['skills_text:run'])).toEqual([]);
  expect(suggestedEvidence([{ ...ref, payloadExpiresAt: '2000-01-01' }], packet)).toEqual([]);
});
it('suggests the sole eligible shared completed result, not a failed or ambiguous result', () => {
  expect(suggestedEvidence([ref], packet)).toHaveLength(1);
  expect(suggestedEvidence([{ ...ref, status: 'failed' }], packet)).toEqual([]);
  expect(suggestedEvidence([ref, { ...ref, sourceId: 'other' }], packet)).toEqual([]);
});
it('only permits approval of pending requests while preserving release of an approved revision', () => {
  render(<ReleaseRequestPanel scopeKey="reviewer" state="ready" preview={{ packet, fingerprint: 'fp', current: true }} review={{ complete: true, dependencyChanges: [] }} request={{ id: 'request', status: 'approved', revision: 1 }} permissions={{ canDecide: true, canExecute: true }} reviewedIdentity="1:fp" onDecide={vi.fn()} onExecute={vi.fn()} />);
  expect(screen.queryByRole('button', { name: 'Approve release' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Release to production' })).toBeEnabled();
});
