import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { skillsAccess, useSkillsAccess } from './access';

const hooks = vi.hoisted(() => ({ scope: {}, org: {}, workspace: {} }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ scope: hooks.scope }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => hooks.org }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => hooks.workspace }));

const scope = { organizationId: 'org', workspaceId: 'ws' };
const at = (orgLevel, wsLevel, overrides = {}) => skillsAccess({
  scope,
  organization: { isReady: true, currentOrganizationId: 'org', orgLevel, ...overrides.organization },
  workspace: { isReady: true, currentWorkspaceId: 'ws', wsLevel, ...overrides.workspace },
});

describe('skillsAccess', () => {
  it.each([
    [1, null, { level: 1, isViewer: true, canWrite: false, canAdmin: false }],
    [null, 3, { level: 3, isViewer: false, canWrite: true, canAdmin: false }],
    [1, 8, { level: 8, isViewer: false, canWrite: true, canAdmin: true }],
    [15, 1, { level: 15, isViewer: false, canWrite: true, canAdmin: true }],
  ])('uses the higher of org level %s and workspace level %s', (orgLevel, wsLevel, expected) => {
    expect(at(orgLevel, wsLevel)).toEqual({ sameScope: true, ready: true, ...expected });
  });
  it('decides nothing while contexts load, levels are unknown, or the role belongs to another scope', () => {
    const undecided = { sameScope: false, ready: false, level: 0, isViewer: false, canWrite: false, canAdmin: false };
    expect(at(8, 8, { organization: { isReady: false } })).toEqual(undecided);
    expect(at(null, null)).toEqual({ ...undecided, sameScope: true });
    expect(at(8, 8, { workspace: { currentWorkspaceId: 'old-ws' } })).toEqual(undecided);
    expect(at(8, 8, { organization: { currentOrganizationId: 'other-org' } })).toEqual(undecided);
    expect(skillsAccess({ scope: {}, organization: { isReady: true }, workspace: { isReady: true, wsLevel: 8 } })).toEqual(undecided);
  });
  it('reads the live Skills scope and selected contexts through the hook', () => {
    hooks.scope = scope;
    hooks.org = { isReady: true, currentOrganizationId: 'org', orgLevel: 1 };
    hooks.workspace = { isReady: true, currentWorkspaceId: 'ws', wsLevel: 1 };
    const { result, rerender } = renderHook(() => useSkillsAccess());
    expect(result.current).toMatchObject({ ready: true, isViewer: true, canWrite: false });
    hooks.workspace = { ...hooks.workspace, wsLevel: 8 };
    rerender();
    expect(result.current).toMatchObject({ isViewer: false, canWrite: true, canAdmin: true });
  });
});
