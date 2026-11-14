import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
const access = vi.hoisted(() => ({ isViewer: false }));
vi.mock('../utils/access', () => ({ useSkillsAccess: () => access }));
import ContextUsePanel from './ContextUsePanel';
afterEach(() => { cleanup(); access.isViewer = false; });
const props = {
  scopeKey: 'scope', state: 'ready', productionVersion: 1, viewingVersion: 2,
  snapshot: { name: 'support-agent', kind: 'agent_context', version: 1, versionId: '11111111-1111-4111-8111-111111111111', digest: `sha256:${'a'.repeat(64)}` },
  origin: 'https://api.example.invalid', organizationId: '22222222-2222-4222-8222-222222222222', workspaceId: '33333333-3333-4333-8333-333333333333',
};
it('labels the preview honestly and keeps both copy actions secondary when a key is missing', () => {
  render(<ContextUsePanel {...props} />);
  expect(screen.getByText('Preview: not yet tested with Claude Code')).toBeVisible();
  expect(screen.getByText('Create a key first')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy plan command' })).toHaveClass('MuiButton-outlined');
  expect(screen.getByRole('button', { name: 'Copy apply command' })).toHaveClass('MuiButton-outlined');
  expect(screen.getByLabelText('Context install plan command')).toHaveTextContent('--version 1');
});
it('makes choosing the non-production release explicit without changing the current plan itself', () => {
  const onUseViewed = vi.fn();
  render(<ContextUsePanel {...props} onUseViewed={onUseViewed} />);
  expect(screen.getByText(/Install defaults to production v1/)).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Install viewed v2 instead' }));
  expect(onUseViewed).toHaveBeenCalledWith(true);
  // Only the controller's exact inspection snapshot can change the command.
  expect(screen.getByLabelText('Context install plan command')).toHaveTextContent('--version 1');
});
it('names the steps in plain words and keeps environment variables and flags under Details', () => {
  render(<ContextUsePanel {...props} />);
  expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual(['1. Choose your tool', '2. Create a read-only key', '3. Preview the changes', '4. Install']);
  expect(screen.queryByText(/Plan and inspect|Needs a key|Choose your client/)).not.toBeInTheDocument();
  expect(screen.queryByText(/set FI_CONTEXT_CREDENTIAL to your read-only key|--allow-preview opts in/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  expect(screen.getByText(/set FI_CONTEXT_CREDENTIAL to your read-only key/)).toBeVisible();
  expect(screen.getByText(/--allow-preview opts in/)).toBeVisible();
});
it('keeps the copied commands complete, with the environment lines and the credential comment', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  render(<ContextUsePanel {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Copy plan command' }));
  await screen.findByText('Copied the complete plan command.');
  expect(writeText.mock.calls[0][0]).toContain("export FI_CONTEXT_ORIGIN='https://api.example.invalid'");
  expect(writeText.mock.calls[0][0]).toContain('--allow-preview');
  expect(writeText.mock.calls[0][0]).toContain('# requires FI_CONTEXT_CREDENTIAL');
});
it('holds back Copy for a viewer with the reason, and offers a request for an admin with no secret', async () => {
  access.isViewer = true;
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  render(<ContextUsePanel {...props} />);
  expect(screen.getByRole('button', { name: 'Copy plan command' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Copy apply command' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Copy plan command' })).toHaveAccessibleDescription('Ask a workspace admin for a read-only key, then come back to copy the commands.');
  expect(screen.queryByText('Create a key first')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Copy request for admin' }));
  await screen.findByText('Copied the request for an admin.');
  expect(writeText).toHaveBeenCalledWith('Please create a read-only key for support-agent v1 in Future AGI.');
  expect(writeText.mock.calls[0][0]).not.toMatch(/FI_CONTEXT|secret/i);
});
const Access = () => null;
it('tells only people who can create keys to create one first', () => {
  const view = render(<ContextUsePanel {...props} runtimeAccess={<Access canIssue={false} />} />);
  expect(screen.queryByText('Create a key first')).not.toBeInTheDocument();
  view.rerender(<ContextUsePanel {...props} runtimeAccess={<Access canIssue />} />);
  expect(screen.getByText('Create a key first')).toBeVisible();
});
