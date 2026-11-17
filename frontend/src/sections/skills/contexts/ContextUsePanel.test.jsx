import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
const access = vi.hoisted(() => ({ isViewer: false }));
vi.mock('../utils/access', () => ({ useSkillsAccess: () => access }));
import ContextUsePanel, { InstallSteps, buildContextPlan } from './ContextUsePanel';
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
it('names the steps in plain words and keeps environment variables, flags and requirements under one Before you install disclosure', () => {
  render(<ContextUsePanel {...props} />);
  expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual(['1. Choose your tool', '2. Create a read-only key', '3. Preview the changes', '4. Install']);
  expect(screen.queryByText(/Plan and inspect|Needs a key|Choose your client/)).not.toBeInTheDocument();
  expect(screen.queryByText(/set FI_CONTEXT_CREDENTIAL to your read-only key|--allow-preview opts in/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Details' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Installation requirements' })).not.toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: 'Before you install' })).toHaveLength(1);
  expect(screen.queryByText(/full digest/)).not.toBeInTheDocument();
  expect(screen.getByText(/confirm the version number \(v1\) in Before you install/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Before you install' }));
  expect(screen.getByText(/set FI_CONTEXT_CREDENTIAL to your read-only key/)).toBeVisible();
  expect(screen.getByText(/Pinned version\./)).toBeVisible();
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
  expect(writeText.mock.calls[0][0]).toContain('# needs your read-only key in FI_CONTEXT_CREDENTIAL');
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

it('never generates a command or snippet that needs the full API keys', () => {
  const { container } = render(<ContextUsePanel {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Before you install' }));
  expect(container.textContent).not.toMatch(/FI_API_KEY|FI_SECRET_KEY|X-Api-Key|X-Secret-Key|unzip|curl/);
  expect(buildContextPlan({ ...props, snapshot: props.snapshot, profile: { id: 'codex' }, project: '.', preview: true })).not.toMatch(/FI_API_KEY|FI_SECRET_KEY/);
});
it('keeps the context command byte for byte, from the shared builder', () => {
  expect(buildContextPlan({ ...props, profile: { id: 'claude-code' }, project: '.', preview: true }).split('\n')).toEqual([
    "export FI_CONTEXT_ORIGIN='https://api.example.invalid'",
    "export FI_CONTEXT_ORGANIZATION_ID='22222222-2222-4222-8222-222222222222'",
    "export FI_CONTEXT_WORKSPACE_ID='33333333-3333-4333-8333-333333333333'",
    '# needs your read-only key in FI_CONTEXT_CREDENTIAL',
    "python -m fi.context plan --name 'support-agent' --version 1 --client 'claude-code' --project '.' --allow-preview --out 'context-plan.json'",
    "python -m fi.context inspect --plan 'context-plan.json' --diff",
  ]);
});
it('renders a skill through the same steps, with the skill command and no context-only switch', () => {
  const skill = { kind: 'skill', name: 'support-policy', label: 'production', version: 1 };
  const { origin, organizationId, workspaceId } = props;
  const view = render(<InstallSteps snapshot={skill} origin={origin} organizationId={organizationId} workspaceId={workspaceId} productionVersion={3} viewingVersion={2} />);
  const skillHeadings = screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent);
  expect(screen.getByLabelText('Skill install plan command')).toHaveTextContent("python -m fi.skills plan --name 'support-policy' --label 'production'");
  expect(screen.queryByRole('checkbox', { name: /Install viewed/ })).not.toBeInTheDocument();
  expect(screen.queryByText(/No production version is assigned/)).not.toBeInTheDocument();
  expect(screen.getByText('Preview: not yet tested with Claude Code')).toBeVisible();
  view.unmount();
  render(<ContextUsePanel {...props} />);
  expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual(skillHeadings);
});
it('passes the footer and after-install slots through for Check tracing and the skill fallback', () => {
  render(<InstallSteps snapshot={props.snapshot} origin={props.origin} organizationId={props.organizationId} workspaceId={props.workspaceId} footer={<p>Footer slot</p>} afterInstall={<p>After install slot</p>} />);
  expect(screen.getByText('Footer slot')).toBeVisible();
  expect(screen.getByText('After install slot')).toBeVisible();
});
