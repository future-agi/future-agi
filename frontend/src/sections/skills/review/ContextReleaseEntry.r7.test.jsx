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
const ref = { source: 'skills_text', sourceId: 'run', subjectId: 'context', subjectKind: 'agent_context', candidateDigest: 'digest', visibility: 'workspace', status: 'completed', outcome: 'works_on_examples', payloadExpiresAt: '2099-01-01', configuration: { text: {} }, annotationRevision: 7 };

it('hands the exact context to the canonical composer without a UUID field or mutation', () => {
  render(<MemoryRouter><ContextLabelRequestPanel contextId="context" version={{ id: 'candidate', number: 2, digest: 'digest' }} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Request release for v2' }));
  const url = new URL(navigated.mock.calls.at(-1)[0], 'https://example.invalid');
  expect(Object.fromEntries(url.searchParams)).toMatchObject({ compose: '1', subject: 'context', name: 'Support agent', candidate: 'candidate', number: '2', digest: 'digest', label: 'production' });
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Request release for v2' }).closest('[aria-label]')).toHaveAttribute('aria-label', expect.stringContaining('An independent reviewer approves'));
  expect(screen.queryByRole('heading')).not.toBeInTheDocument();
});
it('does not carry a stored reviewer across logins or auto-submit for a sole eligible reviewer', () => {
  localStorage.setItem('skills.releaseRequest.lastReviewer:other-login', 'someone-else');
  const submit = vi.fn();
  const props = { scopeKey: 'new-login', state: 'ready', preview: { packet, fingerprint: 'fp', current: true }, review: { complete: true, dependencyChanges: [] }, onSubmit: submit };
  const { rerender } = render(<ReleaseRequestPanel {...props} recipients={[{ id: 'eligible', name: 'Independent reviewer' }]} permissions={{ canSubmit: true }} />);
  expect(screen.getByRole('combobox', { name: 'Assigned reviewer' })).toHaveTextContent('Independent reviewer');
  expect(screen.getByRole('button', { name: 'Send request' })).toBeDisabled();
  expect(submit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('button', { name: 'Send request' })).toBeEnabled();
  rerender(<ReleaseRequestPanel {...props} scopeKey="another-login" recipients={[]} permissions={{ canSubmit: true }} />);
  expect(screen.getByRole('button', { name: 'Send request' })).toBeDisabled();
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});
it('resolves carried refs against fresh options, never URL annotation revisions', () => {
  expect(suggestedEvidence([ref], packet, ['skills_text:run'])).toEqual([{ source: 'skills_text', sourceId: 'run', annotationRevision: 7 }]);
  expect(suggestedEvidence([{ ...ref, visibility: 'private' }], packet, ['skills_text:run'])).toEqual([]);
  expect(suggestedEvidence([{ ...ref, candidateDigest: 'different' }], packet, ['skills_text:run'])).toEqual([]);
  expect(suggestedEvidence([{ ...ref, payloadExpiresAt: '2000-01-01' }], packet)).toEqual([]);
});
it('suggests only an eligible Application test matching the target, not an uncarried Answer check', () => {
  const native = { ...ref, source: 'simulation_native', configuration: { native: {}, digest: 'config' } };
  const targetPacket = { ...packet, target: { configurationDigest: 'config' } };
  expect(suggestedEvidence([ref], targetPacket)).toEqual([]);
  expect(suggestedEvidence([native], targetPacket)).toHaveLength(1);
  expect(suggestedEvidence([{ ...native, status: 'failed' }], targetPacket)).toEqual([]);
  expect(suggestedEvidence([native, { ...native, sourceId: 'other' }], targetPacket)).toEqual([]);
});
it('only permits approval of pending requests while preserving release of an approved revision', () => {
  render(<ReleaseRequestPanel scopeKey="reviewer" state="ready" preview={{ packet, fingerprint: 'fp', current: true }} review={{ complete: true, dependencyChanges: [] }} request={{ id: 'request', status: 'approved', revision: 1 }} permissions={{ canDecide: true, canExecute: true }} reviewedIdentity="1:fp" onDecide={vi.fn()} onExecute={vi.fn()} />);
  expect(screen.queryByRole('button', { name: 'Approve release' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Release to production' })).toBeEnabled();
});

it('does not offer a release request for the production version', () => {
  const { container } = render(<MemoryRouter><ContextLabelRequestPanel contextId="context" version={{ id: 'v1', number: 1, digest: 'digest' }} /></MemoryRouter>);
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByRole('button', { name: 'Request release for v2' })).not.toBeInTheDocument();
});

it('never ranks a newer unrecorded result above a recorded pass', () => {
  const passing = { ...ref, source: 'simulation_native', configuration: { native: {}, digest: 'config' }, completedAt: '2026-11-01' };
  const unrecorded = { ...passing, sourceId: 'unrecorded', outcome: undefined, completedAt: '2026-11-14' };
  const targetPacket = { ...packet, target: { configurationDigest: 'config' } };
  expect(suggestedEvidence([unrecorded, passing], targetPacket)).toEqual([{ source: 'simulation_native', sourceId: 'run', annotationRevision: 7 }]);
  expect(suggestedEvidence([unrecorded], targetPacket)).toEqual([]);
});

it('is a small text button in the Versions row, never an outlined or contained button', () => {
  render(<MemoryRouter><ContextLabelRequestPanel contextId="context" version={{ id: 'candidate', number: 2, digest: 'digest' }} /></MemoryRouter>);
  const button = screen.getByRole('button', { name: 'Request release for v2' });
  expect(button).toHaveClass('MuiButton-text');
  expect(button).toHaveClass('MuiButton-sizeSmall');
});
