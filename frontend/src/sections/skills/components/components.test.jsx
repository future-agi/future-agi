import { lazy } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import SkillsWorkspaceLayout from './SkillsWorkspaceLayout';
import { iconExists } from '@iconify/react';
import FileTree from './FileTree';
import SkillMarkdownPreview from './SkillMarkdownPreview';
import TextDiff from './TextDiff';
import ChecksList from './ChecksList';
import { SKILLS_NAV_ICON } from './skillIcons';
import { buildDiffRows } from './diffRows';

// Shared dialogs require the product theme; their interaction boundary is enough
// here, while real shared-dialog tests cover focus trapping and close buttons.
vi.mock('src/components/ModalWrapper/ModalWrapper', async () => {
  const { default: Types } = await import('prop-types');
  function Modal({ open, title, children, onSubmit, isValid, onClose }) {
    return open ? <section aria-label={title}>{children}<button disabled={!isValid} onClick={onSubmit}>Confirm</button><button onClick={onClose}>Cancel</button></section> : null;
  }
  Modal.propTypes = { open: Types.bool, title: Types.string, children: Types.node, onSubmit: Types.func, isValid: Types.bool, onClose: Types.func };
  return { default: Modal };
});
vi.mock('src/components/custom-dialog', async () => {
  const { default: PropTypes } = await import('prop-types');
  function ConfirmDialog({ open, title, action }) { return open ? <section aria-label={title}>{action}</section> : null; }
  ConfirmDialog.propTypes = { open: PropTypes.bool, title: PropTypes.string, action: PropTypes.node };
  return { ConfirmDialog };
});
vi.mock('src/components/status-badge', async () => {
  const { default: Types } = await import('prop-types');
  function Badge({ label }) { return <span>{label}</span>; }
  Badge.propTypes = { label: Types.string };
  return { default: Badge };
});
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
    expect(screen.queryByRole('button', { name: 'Rename file' })).not.toBeInTheDocument();
    rerender(<FileTree files={files} selectedPath="SKILL.md" onSelect={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} readOnly={false} />);
    expect(screen.getByRole('button', { name: 'Rename file' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Delete file' })).toBeDisabled();
  });
  it('creates a file using a folder prefix without persisting empty folders', () => {
    const create = vi.fn();
    render(<FileTree files={files} onSelect={vi.fn()} onCreate={create} readOnly={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add file' }));
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByRole('combobox', { name: 'File path' })).toHaveValue('');
    fireEvent.change(screen.getByRole('combobox', { name: 'File path' }), { target: { value: 'scripts/check.sh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(create).toHaveBeenCalledWith('scripts/check.sh');
  });
  it('rejects traversal, case duplicates and file/folder collisions', () => {
    render(<FileTree files={files} onSelect={vi.fn()} onCreate={vi.fn()} readOnly={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add file' }));
    for (const value of ['../outside', 'skill.md', 'references', 'SKILL.md/child', 'a\\b']) {
      fireEvent.change(screen.getByRole('combobox', { name: 'File path' }), { target: { value } });
      expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
    }
  });
  it('renames and deletes an ordinary file', () => {
    const rename = vi.fn(); const remove = vi.fn();
    render(<FileTree files={files} selectedPath="z.md" onSelect={vi.fn()} onRename={rename} onDelete={remove} readOnly={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Rename file' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'File path' }), { target: { value: 'notes.md' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(rename).toHaveBeenCalledWith('z.md', 'notes.md');
    fireEvent.click(screen.getByRole('button', { name: 'Delete file' }));
    fireEvent.click(screen.getByText('Delete file'));
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
    expect(screen.getByText('Both versions of this file are empty.')).toBeInTheDocument();
    rerender(<TextDiff fromTitle="New file" toTitle="Your draft" />);
    expect(screen.getByText('New empty file.')).toBeInTheDocument();
    rerender(<TextDiff fromMissing fromTitle="v1" toTitle="Your draft" />);
    expect(screen.getByText('New empty file.')).toBeInTheDocument();
    expect(screen.queryByText(/Both/)).not.toBeInTheDocument();
    rerender(<TextDiff toMissing fromTitle="v2" toTitle="Your draft" />);
    expect(screen.getByText('Removed an empty file.')).toBeInTheDocument();
    // One empty side with content on the other is a real diff, never an "empty" summary.
    rerender(<TextDiff fromText="" toText={'Rules\n'} fromTitle="v2" toTitle="Your draft" />);
    expect(screen.getByRole('cell', { name: 'After line 1, added' })).toBeInTheDocument();
    expect(screen.queryByText(/empty/)).not.toBeInTheDocument();
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

it('registers shared controls and tree arrows locally for offline use', () => {
  for (const icon of [SKILLS_NAV_ICON, 'solar:alt-arrow-right-linear', 'solar:alt-arrow-down-linear', 'solar:copy-linear', 'solar:menu-dots-bold', 'solar:info-circle-linear', 'solar:wallet-linear', 'solar:archive-linear']) expect(iconExists(icon)).toBe(true);
});

const navigation = vi.hoisted(() => ({ bindings: {}, enabled: true, campaigns: false, contract: true, allowance: true }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ enabled: navigation.enabled, identity: 'workspace-a' }) }));
vi.mock('src/api/skills/r3-spend', () => ({ spendPolicyReady: () => navigation.allowance }));
vi.mock('src/api/skills/context-campaigns', () => ({ campaignsContractReady: () => navigation.contract, useContextCampaignAvailability: () => ({ data: { enabled: navigation.campaigns } }) }));
vi.mock('../contexts/ContextLibraryView', () => ({ useContextAuthoring: () => ({ bindings: navigation.bindings }) }));
vi.mock('./SkillsProductionProvider', async () => {
  const { default: Types } = await import('prop-types');
  function Provider({ children }) { return children; }
  Provider.propTypes = { children: Types.node };
  return { default: Provider };
});

function mountNavigation(path = '/dashboard/skills', child = <p>Skills content</p>) {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/dashboard/skills" element={<SkillsWorkspaceLayout />}>
      <Route index element={child} />
      <Route path="allowance" element={<p>Allowance content</p>} />
      <Route path="archived" element={<p>Archive content</p>} />
      <Route path="contexts/rollouts" element={<p>Rollout direct route</p>} />
      <Route path="contexts/*" element={<p>Context content</p>} />
    </Route>
  </Routes></MemoryRouter>);
}

describe('Skills workspace navigation', () => {
  beforeEach(() => {
    Object.assign(navigation, { bindings: { library: true, detail: true, editor: true, reviews: true, archived: true }, enabled: true, campaigns: false, contract: true, allowance: true });
  });
  it('shows only primary enabled destinations and keeps secondary pages in More', async () => {
    const user = userEvent.setup();
    mountNavigation();
    expect(within(screen.getByRole('tablist', { name: 'Skills views' })).getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Skills', 'Agent contexts', 'Reviews']);
    expect(screen.queryByText(/unavailable/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'More Skills pages' }));
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Workspace allowance', 'Archived skills']);
    await user.click(screen.getByRole('menuitem', { name: 'Archived skills' }));
    expect(await screen.findByText('Archive content')).toBeVisible();
    expect(screen.getByRole('button', { name: 'More Skills pages' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('tab', { selected: true })).not.toBeInTheDocument();
  });
  it('supports Tab, Enter and Escape with focus restored to the More trigger', async () => {
    const user = userEvent.setup();
    mountNavigation();
    await user.tab();
    expect(screen.getByRole('tab', { name: 'Skills' })).toHaveFocus();
    await user.tab();
    const trigger = screen.getByRole('button', { name: 'More Skills pages' });
    expect(trigger).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('menu')).toBeVisible();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
  it('omits More when neither secondary destination is available', () => {
    navigation.allowance = false; navigation.bindings.archived = false;
    mountNavigation();
    expect(screen.queryByRole('button', { name: 'More Skills pages' })).not.toBeInTheDocument();
  });
  it('requires every context capability and a review capability', () => {
    navigation.bindings = { library: true, detail: true };
    mountNavigation();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Skills']);
  });
  it('shows rollouts only when both the contract and server capability are ready', () => {
    navigation.campaigns = true; navigation.contract = false;
    const { unmount } = mountNavigation();
    expect(screen.queryByRole('tab', { name: 'Repository rollouts' })).not.toBeInTheDocument();
    unmount(); navigation.contract = true;
    mountNavigation('/dashboard/skills/contexts/rollouts');
    expect(screen.getByRole('tab', { name: 'Repository rollouts' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Skills', 'Agent contexts', 'Reviews', 'Repository rollouts']);
  });
  it('does not remove a direct rollout route or misleadingly select the context tab when off', () => {
    mountNavigation('/dashboard/skills/contexts/rollouts');
    expect(screen.getByText('Rollout direct route')).toBeVisible();
    expect(screen.queryByRole('tab', { selected: true })).not.toBeInTheDocument();
  });
  it('keeps the shell mounted while a lazy destination loads', async () => {
    let resolve;
    const LazyPage = lazy(() => new Promise((done) => { resolve = done; }));
    render(<MemoryRouter initialEntries={['/dashboard/skills']}><Routes><Route path="/dashboard/skills" element={<SkillsWorkspaceLayout />}>
      <Route index element={<Link to="contexts">Open contexts</Link>} />
      <Route path="contexts" element={<LazyPage />} />
    </Route></Routes></MemoryRouter>);
    const shell = screen.getByRole('navigation', { name: 'Skills navigation' });
    fireEvent.click(screen.getByRole('link', { name: 'Open contexts' }));
    expect(screen.getByRole('progressbar', { name: 'Loading' })).toBeVisible();
    expect(screen.getByRole('navigation', { name: 'Skills navigation' })).toBe(shell);
    await act(async () => resolve({ default: () => <p>Loaded destination</p> }));
    expect(await screen.findByText('Loaded destination')).toBeVisible();
    expect(screen.getByRole('navigation', { name: 'Skills navigation' })).toBe(shell);
  });
});
