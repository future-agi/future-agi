import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { iconExists } from '@iconify/react';
import FileTree from './FileTree';
import SkillMarkdownPreview from './SkillMarkdownPreview';
import TextDiff from './TextDiff';
import ChecksList from './ChecksList';
import { SKILLS_NAV_ICON } from './skillIcons';
import { buildDiffRows } from './diffRows';

// Shared dialogs require the product theme; their interaction boundary is enough
// here, while real shared-dialog tests cover focus trapping and close buttons.
vi.mock('src/components/ModalWrapper/ModalWrapper', () => ({ default: ({ open, title, children, onSubmit, isValid, onClose }) => open ? <section aria-label={title}>{children}<button disabled={!isValid} onClick={onSubmit}>Confirm</button><button onClick={onClose}>Cancel</button></section> : null }));
vi.mock('src/components/custom-dialog', async () => {
  const { default: PropTypes } = await import('prop-types');
  function ConfirmDialog({ open, title, action }) { return open ? <section aria-label={title}>{action}</section> : null; }
  ConfirmDialog.propTypes = { open: PropTypes.bool, title: PropTypes.string, action: PropTypes.node };
  return { ConfirmDialog };
});
vi.mock('src/components/status-badge', () => ({ default: ({ label }) => <span>{label}</span> }));
afterEach(cleanup);
const files = [{ path: 'references/policy.md' }, { path: 'SKILL.md' }, { path: 'z.md' }];

describe('FileTree', () => {
  it('supports roving arrows, collapse/expand, parent, Home/End and Enter', () => {
    const select = vi.fn();
    render(<FileTree files={files} selectedPath="SKILL.md" onSelect={select} />);
    const root = screen.getByRole('treeitem', { name: 'SKILL.md' });
    const folder = screen.getByRole('treeitem', { name: 'references' });
    act(() => root.focus());
    fireEvent.keyDown(root, { key: 'ArrowDown' });
    expect(folder).toHaveFocus();
    fireEvent.keyDown(folder, { key: 'ArrowRight' });
    const policy = screen.getByRole('treeitem', { name: 'policy.md' });
    expect(policy).toHaveFocus();
    fireEvent.keyDown(policy, { key: 'Enter' });
    expect(select).toHaveBeenCalledWith('references/policy.md');
    fireEvent.keyDown(policy, { key: 'ArrowLeft' });
    expect(folder).toHaveFocus();
    fireEvent.keyDown(folder, { key: 'ArrowLeft' });
    expect(screen.queryByRole('treeitem', { name: 'policy.md' })).not.toBeInTheDocument();
    expect(folder).toHaveAttribute('aria-expanded', 'false');
    fireEvent.keyDown(folder, { key: 'ArrowRight' });
    expect(screen.getByRole('treeitem', { name: 'policy.md' })).toBeInTheDocument();
    fireEvent.keyDown(folder, { key: 'End' });
    expect(screen.getByRole('treeitem', { name: 'z.md' })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('treeitem', { name: 'z.md' }), { key: 'Home' });
    expect(root).toHaveFocus();
  });
  it('protects SKILL.md and hides editing actions in read-only mode', () => {
    const { rerender } = render(<FileTree files={files} selectedPath="SKILL.md" onSelect={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Rename' })).not.toBeInTheDocument();
    rerender(<FileTree files={files} selectedPath="SKILL.md" onSelect={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} readOnly={false} />);
    expect(screen.getByRole('button', { name: 'Rename' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
  });
  it('creates a file using a folder prefix without persisting empty folders', () => {
    const create = vi.fn();
    render(<FileTree files={files} onSelect={vi.fn()} onCreate={create} readOnly={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'New folder' }));
    fireEvent.change(screen.getByLabelText('Folder path'), { target: { value: 'scripts' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'New file' }));
    expect(screen.getByLabelText('File path')).toHaveValue('scripts/');
    fireEvent.change(screen.getByLabelText('File path'), { target: { value: 'scripts/check.sh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(create).toHaveBeenCalledWith('scripts/check.sh');
  });
  it('rejects traversal, case duplicates and file/folder collisions', () => {
    render(<FileTree files={files} onSelect={vi.fn()} onCreate={vi.fn()} readOnly={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'New file' }));
    for (const value of ['../outside', 'skill.md', 'references', 'SKILL.md/child', 'a\\b']) {
      fireEvent.change(screen.getByLabelText('File path'), { target: { value } });
      expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
    }
  });
  it('renames and deletes an ordinary file', () => {
    const rename = vi.fn(); const remove = vi.fn();
    render(<FileTree files={files} selectedPath="z.md" onSelect={vi.fn()} onRename={rename} onDelete={remove} readOnly={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.change(screen.getByLabelText('File path'), { target: { value: 'notes.md' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(rename).toHaveBeenCalledWith('z.md', 'notes.md');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete file' }));
    expect(remove).toHaveBeenCalledWith('z.md');
  });
});

describe('SkillMarkdownPreview', () => {
  it('renders YAML separately, GFM, literal variables, and relative links', () => {
    const open = vi.fn();
    render(<SkillMarkdownPreview currentPath="references/a.md" onOpenPath={open} content={'---\nname: support-policy\ndescription: Use when reviewing support requests.\n---\n## Instructions\n{{ticket}}\n[Root](../SKILL.md#intro) and [Here](#intro)\n\n~~removed~~'} />);
    expect(screen.getAllByRole('definition')).toHaveLength(2);
    expect(screen.getByText('support-policy')).toBeInTheDocument();
    expect(screen.getByText('{{ticket}}', { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Root' }));
    expect(open).toHaveBeenCalledWith('SKILL.md');
    fireEvent.click(screen.getByRole('button', { name: 'Here' }));
    expect(open).toHaveBeenCalledWith('references/a.md');
    expect(screen.getByText('removed').tagName).toBe('DEL');
  });
  it('secures external links, disables traversal/raw HTML and avoids remote image loads', () => {
    const { container } = render(<SkillMarkdownPreview content={'[External](https://example.invalid) [Escape](../private) [Unsafe](javascript:alert)\n<script>alert(1)</script>\n![Tracking](https://example.invalid/pixel.png)'} />);
    expect(screen.getByRole('link', { name: 'External' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.queryByRole('button', { name: 'Escape' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Unsafe' })).not.toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
  });
  it('keeps malformed YAML readable rather than throwing', () => {
    render(<SkillMarkdownPreview content={'---\nname: [invalid\n---\nBody'} />);
    expect(screen.getByText('Body')).toBeInTheDocument();
  });
});

describe('TextDiff', () => {
  it('aligns additions, removals and unchanged lines with separate numbers', () => {
    const rows = buildDiffRows('same\nold\nextra\n', 'same\nnew\n');
    expect(rows).toHaveLength(3);
    expect(rows[0].left).toMatchObject({ text: 'same', number: 1, status: 'unchanged' });
    expect(rows[1].right).toMatchObject({ text: 'new', number: 2, status: 'added' });
    expect(rows[2].right).toBeNull();
    render(<TextDiff fromText={'same\nold\nextra\n'} toText={'same\nnew\n'} fromTitle="v1" toTitle="Draft" />);
    expect(screen.getAllByRole('row')).toHaveLength(4);
    expect(screen.getByRole('columnheader', { name: 'Draft' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: 'After, no line' })).toHaveAttribute('data-status', 'filler');
  });
  it('handles empty files and missing final newlines', () => {
    const { rerender } = render(<TextDiff />);
    expect(screen.getByText('Both files are empty.')).toBeInTheDocument();
    rerender(<TextDiff fromText={'one\n'} toText="one" />);
    expect(screen.getByText('After has no newline at the end of the file.')).toBeInTheDocument();
  });
});

it('groups checks by severity and forwards the original check', () => {
  const select = vi.fn();
  const check = { code: 'root_missing', severity: 'error', message: 'Add SKILL.md.', path: 'SKILL.md', line: 1 };
  render(<ChecksList checks={{ items: [{ code: 'x', severity: 'info', message: 'Review scripts.' }, check, { code: 'y', severity: 'warning', message: 'Check this link.' }] }} onSelect={select} />);
  expect(screen.getAllByRole('region').map((item) => item.getAttribute('aria-label'))).toEqual(['Errors', 'Warnings', 'Notes']);
  fireEvent.click(within(screen.getByRole('region', { name: 'Errors' })).getByRole('button'));
  expect(select).toHaveBeenCalledWith(check);
});

it('registers the nav icon and tree arrows locally for offline use', () => {
  expect(iconExists(SKILLS_NAV_ICON)).toBe(true);
  expect(iconExists('solar:alt-arrow-right-linear')).toBe(true);
  expect(iconExists('solar:alt-arrow-down-linear')).toBe(true);
});
