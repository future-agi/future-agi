import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import ReleaseReviewBody, { ChangedFiles, PRIVATE_RESULTS_NOTE, checkedFiles } from './ReleaseReviewBody';

// UXF-05: changed files are open (all when 3 or fewer, else the first plus
// "Show N more changed files"); unchanged files are collapsed behind
// "Show N unchanged files". UXF-12: the evidence row opens Inspect.

afterEach(cleanup);

const file = (path, status = 'modified') => ({ path, status, fromText: 'a', toText: status === 'unchanged' ? 'a' : 'b' });
const packet = { subjectId: 'context', subjectKind: 'agent_context', candidateVersionId: 'version', candidateDigest: 'digest', target: null, externalPipeline: null,
  transition: { label: 'production', expectedVersion: 1, version: 2, expectedReleaseRevision: 3, expectedSecurityRevision: 0 },
  evidence: [{ source: 'skills_text', sourceId: 'run', status: 'completed', visibility: 'workspace', payloadExpiresAt: '2999-01-01', outcome: 'works_on_examples', annotationRevision: 0, configuration: { text: {} } }] };
const disclosures = (open) => screen.queryAllByRole('button').filter((button) => / · (modified|added|unchanged)$/.test(button.textContent) && button.getAttribute('aria-expanded') === String(open));

describe('ReleaseReviewBody files', () => {
  it('opens every changed file when there are three or fewer and collapses unchanged ones', () => {
    render(<ReleaseReviewBody packet={packet} changes={[file('AGENTS.md'), file('b.md', 'added'), file('c.md', 'unchanged')]} dependencyChanges={[]} checks={{ items: [], summary: { errors: 0, warnings: 0 } }} />);
    expect(disclosures(true).map((button) => button.textContent)).toEqual(['AGENTS.md · modified', 'b.md · added']);
    expect(screen.queryByRole('button', { name: 'c.md · unchanged' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show 1 unchanged file' }));
    expect(screen.getByRole('button', { name: 'c.md · unchanged' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('opens the first of many changed files and offers the rest', () => {
    render(<ReleaseReviewBody packet={packet} changes={['AGENTS.md', 'b.md', 'c.md', 'd.md'].map((path) => file(path))} dependencyChanges={[]} checks={{ items: [], summary: { errors: 0, warnings: 0 } }} />);
    expect(disclosures(true).map((button) => button.textContent)).toEqual(['AGENTS.md · modified']);
    fireEvent.click(screen.getByRole('button', { name: 'Show 3 more changed files' }));
    expect(disclosures(true)).toHaveLength(4);
  });

  it('counts the files a person checks, leaving the generated settings file out', () => {
    expect(checkedFiles([file('AGENTS.md'), file('.futureagi/context.json'), file('x.md', 'unchanged')])).toHaveLength(1);
    const renderFile = vi.fn((item) => item.path);
    render(<ChangedFiles files={[file('a.md'), file('b.md', 'unchanged')]} renderFile={renderFile} />);
    expect(renderFile).toHaveBeenCalledWith(expect.objectContaining({ path: 'a.md' }), { open: true });
    expect(renderFile).not.toHaveBeenCalledWith(expect.objectContaining({ path: 'b.md' }), expect.anything());
  });

  it('opens a test result from its row, with Inspect in the ⋯ menu', () => {
    render(<ReleaseReviewBody packet={packet} changes={[]} dependencyChanges={[]} checks={{ items: [], summary: { errors: 0, warnings: 0 } }} />);
    expect(screen.queryByRole('button', { name: 'Inspect' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Actions for / })).toBeVisible();
  });

  it('says once that private results need sharing', () => {
    expect(PRIVATE_RESULTS_NOTE).toBe('Private results are visible only to you; sharing lets the reviewer see them.');
  });
});
