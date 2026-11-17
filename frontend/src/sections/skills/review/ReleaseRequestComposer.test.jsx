import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReleaseRequestComposer from './ReleaseRequestComposer';
import { PRIVATE_RESULTS_NOTE } from './ReleaseReviewBody';
import { useReviewOptions, useShareReleaseEvidence } from 'src/api/skills/review-authoring';
import { RETIRED_TERMS } from '../utils/terms';

// UXF-17: one bulk share button, a per-row consent dialog, "From your test"
// pre-ticked (and never attached before consent), and Continue disabled with
// "Choose a reviewer". UXF-14: "Request release for v2" / "Continue".

vi.mock('src/api/skills/testing', async (load) => ({ ...(await load()), useSkillV11Scope: () => ({ scope: { userId: 'author' } }) }));
vi.mock('src/api/skills/review-authoring', () => ({
  useReviewSubjects: () => ({ data: { results: [], total_pages: 1 } }),
  useReviewCandidates: () => ({ data: { results: [], total_pages: 1 } }),
  useReviewOptions: vi.fn(), usePrepareReleaseReview: () => ({ mutateAsync: vi.fn() }), useSubmitReleaseReview: () => ({}), useShareReleaseEvidence: vi.fn(),
}));

const base = { subjectId: 'context', subjectKind: 'agent_context', candidateDigest: 'digest', configuration: { digest: 'config', text: {} }, annotationRevision: 2,
  status: 'completed', payloadExpiresAt: '2999-01-01', outcome: 'works_on_examples', counts: { cases: 1, graded: 2, planned: 2, regressed: 0 }, source: 'skills_text' };
const fromTest = { ...base, sourceId: 'run-from-test', name: 'Your answer check', visibility: 'private' };
const other = { ...base, sourceId: 'run-other', name: 'Older answer check', visibility: 'private' };
const people = [{ id: 'priya', name: 'Priya Shah' }, { id: 'sam', name: 'Sam Lee' }];
const prefill = { kind: 'agent_context', subjectId: 'context', name: 'support-agent', candidate: { id: 'version', digest: 'digest', number: 2 }, evidence: ['skills_text:run-from-test'] };

const share = vi.fn();
let options;
const shared = (...ids) => ({ targets: [], recipients: people, evidence: [fromTest, other].map((ref) => (ids.includes(ref.sourceId) ? { ...ref, visibility: 'workspace' } : ref)) });

beforeEach(() => {
  vi.clearAllMocks();
  share.mockResolvedValue({});
  options = { data: shared(), isFetching: false, refetch: vi.fn() };
  useReviewOptions.mockImplementation(() => options);
  useShareReleaseEvidence.mockReturnValue({ mutateAsync: share });
});
afterEach(cleanup);

const renderComposer = () => render(<MemoryRouter><ReleaseRequestComposer scopeKey="scope" prefill={prefill} /></MemoryRouter>);
const attachBox = (title) => screen.getByRole('checkbox', { name: `Attach ${title}` });

describe('ReleaseRequestComposer', () => {
  it('pre-ticks the run you came from without attaching or sharing it, and offers one bulk button', () => {
    renderComposer();
    expect(screen.getByRole('heading', { name: 'Request release for v2' })).toBeVisible();
    expect(screen.getByText(PRIVATE_RESULTS_NOTE)).toBeVisible();
    expect(screen.getByText('From your test')).toBeVisible();
    expect(attachBox('Your answer check')).toBeChecked();
    expect(attachBox('Older answer check')).not.toBeChecked();
    // One way to share: the single bulk button. No per-row share buttons, no footer "Attach all".
    expect(screen.getAllByRole('button', { name: /share and attach/i })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Share and attach 1 selected' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Attach all' })).not.toBeInTheDocument();
    expect(share).not.toHaveBeenCalled();
    // Not attached until shared: the footer says so without blocking.
    expect(screen.getByText('No test evidence attached')).toBeVisible();
  });

  it('keeps Continue disabled with "Choose a reviewer" until a reviewer is chosen', () => {
    renderComposer();
    const next = screen.getByRole('button', { name: 'Continue' });
    expect(next).toBeDisabled();
    expect(screen.getByText('Choose a reviewer')).toBeVisible();
    expect(next).toHaveAttribute('aria-describedby', 'prepare-review-reason');
  });

  it('opens the share consent for one private row only, and cancelling shares nothing', () => {
    renderComposer();
    fireEvent.click(attachBox('Older answer check'));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Share 1 private result with the workspace?')).toBeVisible();
    expect(within(dialog).getByText('Older answer check')).toBeVisible();
    expect(within(dialog).queryByText('Your answer check')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(share).not.toHaveBeenCalled();
    expect(attachBox('Older answer check')).not.toBeChecked();
  });

  it('attaches a private row only after its sharing is confirmed', async () => {
    options.refetch.mockImplementation(async () => { options = { ...options, data: shared('run-other') }; return { data: options.data }; });
    renderComposer();
    fireEvent.click(attachBox('Older answer check'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Share and attach' }));
    await waitFor(() => expect(share).toHaveBeenCalledTimes(1));
    expect(share).toHaveBeenCalledWith({ source: 'skills_text', sourceId: 'run-other', annotationRevision: 2, acknowledgeSnapshotSharing: true });
    await waitFor(() => expect(attachBox('Older answer check')).toBeChecked());
    expect(screen.queryByText('No test evidence attached')).not.toBeInTheDocument();
  });

  it('the bulk button shares the pre-ticked run in one consent', async () => {
    options.refetch.mockImplementation(async () => { options = { ...options, data: shared('run-from-test') }; return { data: options.data }; });
    renderComposer();
    fireEvent.click(screen.getByRole('button', { name: 'Share and attach 1 selected' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Your answer check')).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Share and attach' }));
    await waitFor(() => expect(share).toHaveBeenCalledWith(expect.objectContaining({ sourceId: 'run-from-test', acknowledgeSnapshotSharing: true })));
    await waitFor(() => expect(screen.queryByRole('button', { name: /selected$/ })).not.toBeInTheDocument());
    expect(attachBox('Your answer check')).toBeChecked();
  });

  it('uses the release request names only', () => {
    renderComposer();
    const text = document.body.textContent;
    for (const retired of RETIRED_TERMS) expect(text).not.toContain(retired);
    expect(text).not.toContain('Prepare review');
  });
});
