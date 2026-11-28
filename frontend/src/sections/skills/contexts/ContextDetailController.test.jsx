import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setSkillTracesOverride } from '../runtime/skillTracesFlag';
import ContextDetailController from './ContextDetailController';

// SKILL-TRACES §7.4, §16 UXF-07 and §10 UXF5 commit 2: the controller supplies the context
// Runtime and Activity sections and Check tracing on Install only with `skillTraces` on.
// The view and every panel are stand-ins; this suite checks only what the controller hands them.

const state = vi.hoisted(() => ({ isViewer: false, sections: null, runtime: vi.fn(), check: vi.fn(), usage: vi.fn() }));
const query = (data) => ({ data, isLoading: false, isError: false, isFetching: false, refetch: vi.fn() });
const version = { id: 'ctx-v2', number: 2, digest: `sha256:${'a'.repeat(64)}` };

vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'org/ws/user', scope: { organizationId: 'org', workspaceId: 'ws' } }) }));
vi.mock('src/api/skills/context-authoring', () => ({
  useContextDetail: () => query({ context: { id: 'ctx-1', name: 'support-agent', latestVersion: 2, labels: [{ name: 'production', version: 2 }] }, releaseState: { permissions: { canPublish: true, canIssueCredentials: false } } }),
  useContextDraft: () => query(undefined),
}));
vi.mock('src/api/skills/context-consumers', () => ({ useContextBindings: () => query({ results: [] }) }));
vi.mock('src/api/skills/context-production', () => ({
  contextReviewReady: () => false,
  useContextVersions: () => query({ results: [version] }),
  useContextInspection: () => query({ version, descriptor: { links: [] }, snapshot: null }),
  useContextInspectionFile: () => query(undefined),
  useContextConsumers: () => query(undefined),
}));
vi.mock('../utils/origin', () => ({ publicApiOrigin: () => ({ origin: 'https://api.example.test', error: null }) }));
vi.mock('../utils/access', () => ({ useSkillsAccess: () => ({ ready: true, isViewer: state.isViewer, canWrite: !state.isViewer }) }));
vi.mock('./ContextDetailView', () => ({ default: (props) => { state.sections = props.sections; return <div>{props.sections.runtime}{props.sections.use}</div>; } }));
vi.mock('./ContextVersionsTable', () => ({ default: () => null }));
vi.mock('./ContextConsumersView', () => ({ default: () => null }));
vi.mock('./ContextUsePanel', () => ({ default: () => <p>Install instructions</p> }));
vi.mock('./ContextPilotCredentials', () => ({ default: () => null }));
vi.mock('./ContextMigrationExport', () => ({ default: () => null }));
vi.mock('./ContextTestsPanel', () => ({ default: () => null }));
vi.mock('./ContextTargetRegistration', () => ({ default: () => null }));
vi.mock('./J6RecoveryPanel', () => ({ default: () => null }));
vi.mock('../detail/UsageTab', () => ({ default: (props) => { state.usage(props); return <p>Context fetches</p>; } }));
vi.mock('../runtime/ContextRuntimeView', () => ({ default: (props) => { state.runtime(props); return <p>Context runtime</p>; } }));
vi.mock('../runtime/CheckTracing', () => ({ default: (props) => { state.check(props); return <p>{props.canCheck ? 'Check tracing' : 'Check tracing (viewer)'}</p>; } }));

const show = () => render(
  <MemoryRouter initialEntries={['/dashboard/skills/contexts/ctx-1?version=2']}>
    <Routes><Route path="/dashboard/skills/contexts/:contextId" element={<ContextDetailController />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => { state.isViewer = false; state.sections = null; state.runtime.mockClear(); state.check.mockClear(); state.usage.mockClear(); });
afterEach(() => { cleanup(); setSkillTracesOverride(null); });

describe('with skillTraces on', () => {
  beforeEach(() => setSkillTracesOverride(true));

  it('supplies the Runtime section for the viewed version and the Activity section for this context', () => {
    show();
    expect(screen.getByText('Context runtime')).toBeInTheDocument();
    expect(state.runtime.mock.calls.at(-1)[0]).toMatchObject({ context: { id: 'ctx-1', name: 'support-agent' }, versionId: 'ctx-v2' });
    expect(Object.keys(state.sections)).toEqual(expect.arrayContaining(['runtime', 'usage']));
  });

  it('puts Check tracing for context loads below the install instructions, disabled for viewers', () => {
    show();
    expect(screen.getByText('Install instructions')).toBeInTheDocument();
    expect(screen.getByText('Check tracing')).toBeInTheDocument();
    expect(state.check.mock.calls.at(-1)[0]).toMatchObject({ kind: 'agent_context', id: 'ctx-1', name: 'support-agent', canCheck: true });
    cleanup();
    state.isViewer = true;
    show();
    expect(screen.getByText('Check tracing (viewer)')).toBeInTheDocument();
  });
});

describe('with skillTraces off', () => {
  beforeEach(() => setSkillTracesOverride(false));

  it('supplies no Runtime or Activity section and no Check tracing', () => {
    show();
    expect(state.sections.runtime).toBeUndefined();
    expect(state.sections.usage).toBeUndefined();
    expect(screen.getByText('Install instructions')).toBeInTheDocument();
    expect(screen.queryByText(/Check tracing/)).not.toBeInTheDocument();
    expect(state.runtime).not.toHaveBeenCalled();
    expect(state.check).not.toHaveBeenCalled();
  });
});
