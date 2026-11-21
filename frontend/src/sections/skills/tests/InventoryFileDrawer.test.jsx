import { webcrypto } from 'node:crypto';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import InventoryFileDrawer from './InventoryFileDrawer';
import { verifyInventoryText } from './verifyInventoryText';
const h = vi.hoisted(() => ({ query: {} }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Query: () => h.query }));
vi.mock('src/api/skills/context-production', () => ({ readContextText: vi.fn() }));
vi.mock('src/api/skills', () => ({ readSkillFile: vi.fn() }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('verifies the complete UTF-8 bytes against the inventory sha256', async () => {
  vi.stubGlobal('crypto', webcrypto);
  await expect(verifyInventoryText('hello', 'sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824')).resolves.toBe('hello');
  await expect(verifyInventoryText('changed', '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824')).rejects.toThrow('does not match');
});
it('does not display bytes when the file cannot be verified and offers Retry', () => {
  const retry = vi.fn();
  h.query = { isError: true, data: undefined, refetch: retry };
  render(<InventoryFileDrawer subjectId="context" subjectKind="agent_context" candidate={{ version: 2 }} bundle={{ skillId: 'context', digest: 'sha256:bundle' }} file={{ path: 'AGENTS.md', sha256: 'sha256:file' }} onClose={() => {}} />);
  expect(screen.getByRole('heading', { name: 'AGENTS.md', level: 2 })).toBeVisible();
  expect(screen.getByText("This file's content isn't available for inspection.")).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(retry).toHaveBeenCalledOnce();
});
