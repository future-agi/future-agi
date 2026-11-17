import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ContextReleaseReview, { publishAcknowledgmentLabel } from './ContextReleaseReview';
import { RETIRED_TERMS } from '../utils/terms';

// UXF-02: publishing asks one tick and still sends both acknowledgment
// fields, bound to the same review fingerprint. UXF-05: changed files are
// open, unchanged ones collapsed. UXF-09: the release rule is explained.
// UXF-14: "Publish and request release".

afterEach(cleanup);

const file = (path, status = 'modified') => ({ path, status, fromText: `${path} before`, toText: status === 'unchanged' ? `${path} before` : `${path} after` });
const review = (overrides = {}) => ({
  subjectId: 'context', revision: 4, candidateDigest: 'sha256:draft', baseVersion: 1, expectedHeadVersion: 1, headDigest: 'sha256:head',
  reviewFingerprint: 'fp-1', headAcknowledged: true, current: true, complete: true, productionVersion: 1,
  changes: [file('AGENTS.md'), file('skills/refund/SKILL.md', 'added')],
  checks: { summary: { errors: 0, warnings: 2 }, items: [{ severity: 'warning', message: 'Long description' }, { severity: 'warning', message: 'Missing example' }] },
  acceptedLock: { links: [] }, capabilities: { changes: [], beforeDigest: 'sha256:a', afterDigest: 'sha256:b' },
  ...overrides,
});
const renderPage = (props) => render(<MemoryRouter><ContextReleaseReview scopeKey="scope" state="ready" name="support-agent" canPublish {...props} /></MemoryRouter>);

describe('ContextReleaseReview publish', () => {
  it('asks one tick naming the changed files and the warnings', () => {
    renderPage({ draftReview: review(), onPublish: vi.fn(), onCarry: vi.fn() });
    const label = "I've checked the 2 changed files against v1 and the 2 check warnings";
    expect(publishAcknowledgmentLabel(review())).toBe(label);
    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
    expect(screen.getByRole('checkbox', { name: label })).not.toBeChecked();
    // The warnings the tick names are listed open above it.
    expect(screen.getByText('Long description')).toBeVisible();
    expect(screen.getByText('Missing example')).toBeVisible();
    expect(publishAcknowledgmentLabel(review({ checks: { summary: { errors: 0, warnings: 0 }, items: [] } }))).toBe("I've checked the 2 changed files against v1");
  });

  it('the one tick sends both acknowledgment fields bound to the review fingerprint', async () => {
    const onPublish = vi.fn().mockResolvedValue({ version: { number: 2 } });
    renderPage({ draftReview: review(), onPublish });
    const publish = screen.getByRole('button', { name: 'Publish version' });
    expect(publish).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(publish).toBeEnabled();
    fireEvent.click(publish);
    await waitFor(() => expect(onPublish).toHaveBeenCalled());
    expect(onPublish.mock.calls[0][0]).toMatchObject({ reviewFingerprint: 'fp-1', acknowledgeWarnings: true, revision: 4, expectedDigest: 'sha256:draft', expectedHeadVersion: 1 });
  });

  it('sends no warning acknowledgment when there are no warnings', async () => {
    const onPublish = vi.fn().mockResolvedValue({ version: { number: 2 } });
    renderPage({ draftReview: review({ checks: { summary: { errors: 0, warnings: 0 }, items: [] } }), onPublish });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Publish version' }));
    await waitFor(() => expect(onPublish).toHaveBeenCalled());
    expect(onPublish.mock.calls[0][0]).toMatchObject({ reviewFingerprint: 'fp-1', acknowledgeWarnings: false });
  });

  it('asks again when the review fingerprint changes', () => {
    const view = renderPage({ draftReview: review(), onPublish: vi.fn() });
    fireEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('checkbox')).toBeChecked();
    view.rerender(<MemoryRouter><ContextReleaseReview scopeKey="scope" state="ready" name="support-agent" canPublish draftReview={review({ reviewFingerprint: 'fp-2' })} onPublish={vi.fn()} /></MemoryRouter>);
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByText('This changed after you ticked it. Check it again.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Publish version' })).toBeDisabled();
  });

  it('opens changed files and collapses unchanged ones', () => {
    renderPage({ draftReview: review({ changes: [file('AGENTS.md'), file('notes.md', 'unchanged'), file('faq.md', 'unchanged')] }), onPublish: vi.fn() });
    const open = () => [...document.querySelectorAll('#review-files [data-file-open="true"]')];
    expect(open()).toHaveLength(1);
    expect(open()[0]).toHaveTextContent('AGENTS.md');
    expect(open()[0].querySelector('[data-status]')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'notes.md · unchanged' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show 2 unchanged files' }));
    // Unchanged files are listed collapsed: a disclosure each, no diff until opened.
    const notes = screen.getByRole('button', { name: 'notes.md · unchanged' });
    expect(notes).toHaveAttribute('aria-expanded', 'false');
  });

  it('shows the first of many changed files with "Show N more changed files"', () => {
    const many = ['a.md', 'b.md', 'c.md', 'd.md', 'e.md'].map((path) => file(path));
    renderPage({ draftReview: review({ changes: many }), onPublish: vi.fn() });
    const open = () => [...document.querySelectorAll('#review-files [data-file-open="true"]')];
    expect(open().map((node) => node.textContent.slice(0, 4))).toEqual(['a.md']);
    fireEvent.click(screen.getByRole('button', { name: 'Show 4 more changed files' }));
    expect(open()).toHaveLength(5);
    expect(screen.getByRole('checkbox', { name: /^I've checked the 5 changed files/ })).toBeVisible();
  });

  it('keeps three or fewer changed files all open', () => {
    renderPage({ draftReview: review({ changes: ['a.md', 'b.md', 'c.md'].map((path) => file(path)) }), onPublish: vi.fn() });
    expect(document.querySelectorAll('#review-files [data-file-open="true"]')).toHaveLength(3);
    expect(screen.queryByRole('button', { name: /more changed file/ })).not.toBeInTheDocument();
  });

  it('explains the release rule and uses the release request names', () => {
    renderPage({ draftReview: review(), onPublish: vi.fn(), onPublishAndRequest: vi.fn() });
    expect(screen.getByTestId('release-rule-note')).toHaveTextContent("Agent context releases need a second person's review, because one release can change several skills.");
    expect(screen.getByRole('button', { name: 'Publish and request release' })).toBeVisible();
    const text = document.body.textContent;
    for (const retired of RETIRED_TERMS) expect(text).not.toContain(retired);
  });

  it('describes protected labels only from the rules it is given', () => {
    renderPage({ draftReview: review({ protectedLabelNames: ['production'] }), onPublish: vi.fn() });
    expect(screen.getByTestId('release-rule-note')).toHaveTextContent("Production is a protected label: only workspace admins can move it, and automation can't.");
  });
});
