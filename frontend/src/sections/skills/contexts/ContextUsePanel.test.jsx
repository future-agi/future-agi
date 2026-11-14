import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ContextUsePanel from './ContextUsePanel';
afterEach(cleanup);
const props = {
  scopeKey: 'scope', state: 'ready', productionVersion: 1, viewingVersion: 2,
  snapshot: { name: 'support-agent', kind: 'agent_context', version: 1, versionId: '11111111-1111-4111-8111-111111111111', digest: `sha256:${'a'.repeat(64)}` },
  origin: 'https://api.example.invalid', organizationId: '22222222-2222-4222-8222-222222222222', workspaceId: '33333333-3333-4333-8333-333333333333',
};
it('labels the preview honestly and keeps both copy actions secondary when a key is missing', () => {
  render(<ContextUsePanel {...props} />);
  expect(screen.getByText('Host setup not yet verified for Claude Code')).toBeVisible();
  expect(screen.getByText('Needs a key')).toBeVisible();
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
