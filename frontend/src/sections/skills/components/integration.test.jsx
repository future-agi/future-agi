import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { dsPalette, dsTypography } from 'src/theme/ds-tokens';
import FileTree from './FileTree';
import SkillMarkdownPreview from './SkillMarkdownPreview';
import TextDiff from './TextDiff';
import ContextSafetyDialog from '../contexts/ContextSafetyDialog';
import RecoverableDraftChanges from '../editor/RecoverableDraftChanges';

const theme = createTheme({ palette: dsPalette('light'), typography: dsTypography });
const show = (content) => render(<ThemeProvider theme={theme}>{content}</ThemeProvider>);
const digest = `sha256:${'a'.repeat(64)}`;
const recoveryDigest = `sha256:${'b'.repeat(64)}`;
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('protects the configurable root and mounted nodes without changing the SKILL default', () => {
  const onDelete = vi.fn();
  const files = [{ path: 'AGENTS.md' }, { path: 'skills/leaf/SKILL.md', readOnly: true }, { path: 'references/policy.md' }];
  const props = { files, protectedPaths: ['AGENTS.md'], pinned: ['AGENTS.md'], readOnly: false, onSelect: vi.fn(), onDelete, onRename: vi.fn() };
  const view = show(<FileTree {...props} selectedPath="AGENTS.md" />);
  expect(screen.getByRole('button', { name: 'Delete', exact: true })).toBeDisabled();
  view.rerender(<ThemeProvider theme={theme}><FileTree {...props} selectedPath="skills/leaf/SKILL.md" /></ThemeProvider>);
  expect(screen.getByRole('button', { name: 'Rename', exact: true })).toBeDisabled();
  view.rerender(<ThemeProvider theme={theme}><FileTree {...props} selectedPath="references/policy.md" /></ThemeProvider>);
  expect(screen.getByRole('button', { name: 'Delete', exact: true })).toBeEnabled();
  expect(onDelete).not.toHaveBeenCalled();
});

it('renders AGENTS delimiters as literal Markdown and navigates only within the snapshot', () => {
  const open = vi.fn();
  show(<SkillMarkdownPreview literal currentPath="AGENTS.md" content={'---\ndescription: This is instruction text\n---\n[Policy](references/policy.md)\n<script>secret()</script>\n![Image](https://invalid.example/image.png)'} onOpenPath={open} />);
  expect(screen.getByText(/description: This is instruction text/)).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
  expect(document.querySelector('img')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Policy' }));
  expect(open).toHaveBeenCalledWith('references/policy.md');
});

it('uses one full-width narrow diff with markers and no filler columns', () => {
  vi.stubGlobal('matchMedia', vi.fn((query) => ({ matches: true, media: query, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })));
  show(<TextDiff fromText={'Same\nOld\n'} toText={'Same\nNew\nExtra\n'} fromTitle="Origin v4" toTitle="Candidate r12" />);
  const region = screen.getByRole('region', { name: 'Unified file changes' });
  expect(within(region).getAllByText('Same')).toHaveLength(1);
  expect(within(region).getByLabelText('removed')).toHaveTextContent('−');
  expect(within(region).getAllByLabelText('added')).toHaveLength(2);
  expect(region.querySelector('[data-status="filler"]')).toBeNull();
  expect(screen.queryByRole('table')).toBeNull();
  expect(region).toHaveTextContent('Origin v4 / + Candidate r12');
});

it('requires the retained-label review fingerprint and preserves the reason across refreshed review', async () => {
  const apply = vi.fn(async () => ({ id: 'resource' }));
  const props = { open: true, scopeKey: 'scope', onClose: vi.fn(), canAct: true, action: 'unarchive', resource: { id: 'resource', name: 'policy' }, review: { subjectId: 'resource', releaseRevision: 2 }, onApply: apply };
  const view = show(<ContextSafetyDialog {...props} />);
  expect(screen.getByRole('button', { name: 'Recover original identity' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Reviewed original identity' } });
  view.rerender(<ThemeProvider theme={theme}><ContextSafetyDialog {...props} review={{ ...props.review, reviewFingerprint: digest }} /></ThemeProvider>);
  expect(screen.getByLabelText('Reason')).toHaveValue('Reviewed original identity');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Recover original identity' }));
  await waitFor(() => expect(apply).toHaveBeenCalledOnce());
  expect(apply.mock.calls[0][0]).toEqual({ expectedReleaseRevision: 2, expectedReviewFingerprint: digest, note: 'Reviewed original identity' });
});

it('retries suspension with the saved recovery identity and original key without saving a tombstone', async () => {
  const session = { files: [{ path: 'AGENTS.md', content: 'Policy' }], revision: 7, baseVersion: 4, editSequence: 2, note: '', digest, recoveryDigest };
  session.flush = vi.fn(async () => session);
  const suspend = vi.fn().mockRejectedValueOnce(new Error('Response lost')).mockResolvedValueOnce({ suspended: true });
  show(<RecoverableDraftChanges scopeKey="scope" session={session} onSuspend={suspend} />);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Preserve and suspend draft' }));
  await screen.findByText(/could not be confirmed/);
  fireEvent.click(screen.getByRole('button', { name: 'Preserve and suspend draft' }));
  await waitFor(() => expect(suspend).toHaveBeenCalledTimes(2));
  expect(session.flush).toHaveBeenCalledOnce();
  expect(suspend.mock.calls[0]).toEqual(suspend.mock.calls[1]);
  expect(suspend.mock.calls[0][0].expectedDigest).toBe(recoveryDigest);
});

it('does not substitute compiled digest when the recovery identity is missing', async () => {
  const session = { files: [], revision: 7, baseVersion: 4, editSequence: 0, note: '', digest };
  session.flush = vi.fn(async () => session);
  const suspend = vi.fn();
  show(<RecoverableDraftChanges scopeKey="scope" session={session} onSuspend={suspend} />);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Preserve and suspend draft' }));
  await screen.findByText(/could not be confirmed/);
  expect(suspend).not.toHaveBeenCalled();
});
