/* eslint-disable react/prop-types */
import { createElement as h } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import * as copy from './runtimeCopy';
import AgentPanel, { agentPanelInput } from './AgentPanel';
import CheckTracing from './CheckTracing';
import ContextReleaseCard from './ContextReleaseCard';
import ContextRuntimeView from './ContextRuntimeView';
import FailedLoadsLine, { ContentsWarningLine } from './FailedLoadsLine';
import LinkedLoadsCell, { LibraryLoadsCell, LoadedNotLinkedList } from './LinkedLoadsCell';
import LoadedByTable from './LoadedByTable';
import { MoveStateChip } from './MoveStateChip';
import { MoveCardBody } from './ProductionMoveCard';
import ReceiptLoadsLine from './ReceiptLoadsLine';
import RuntimeDiagnosis, { RuntimeNotes } from './RuntimeDiagnosis';
import RuntimeTab from './RuntimeTab';
import { setSkillTracesOverride } from './skillTracesFlag';
import { MOVE, NOW, ROWS, RUNTIME, SKILL, renderRuntime, success } from './__tests__/fixtures';

const hooks = vi.hoisted(() => ({
  runtime: vi.fn(), comparison: vi.fn(), series: vi.fn(), agent: vi.fn(), context: vi.fn(), contextComparison: vi.fn(), latest: vi.fn(),
}));
vi.mock('src/api/skills/runtime', async (importOriginal) => ({
  ...(await importOriginal()),
  useSkillRuntime: hooks.runtime,
  useSkillComparison: hooks.comparison,
  useSkillRuntimeSeries: hooks.series,
  useRuntimeAgent: hooks.agent,
  useContextRuntime: hooks.context,
  useContextComparison: hooks.contextComparison,
  useLatestLoad: hooks.latest,
}));
vi.mock('src/sections/skills/governance/AdoptionPanel', () => ({
  AuthorizedProjectPicker: ({ label }) => h('div', { role: 'group', 'aria-label': label }),
}));
vi.mock('src/components/iconify', () => ({ default: () => null }));
vi.mock('notistack', async (load) => ({ ...(await load()), enqueueSnackbar: vi.fn() }));

// §9.1 runtimeCopy.test.js (UX-06): the new components render only §2.1
// strings. These words must never appear in their rendered text.
// The module's own list adds "uses", "unused" and "0 agents" to the §9.1 words.
const forbiddenIn = (text) => copy.forbiddenRuntimeWords(text);
const rendered = () => document.body.textContent;

const DIAGNOSES = [
  { state: 'no_tracing_project', consumers: [] },
  { state: 'no_readable_projects', consumers: [] },
  { state: 'never_fetched', consumers: [] },
  { state: 'sdk_cannot_record', consumers: [{ label: 'k', channel: 'sdk', sdkVersion: '0.9.2', tracingMode: 'on', canRecord: false }] },
  { state: 'tracing_unavailable', consumers: [{ label: 'k', channel: 'sdk', sdkVersion: '1.4.0', tracingMode: 'off', canRecord: false }, { label: 'j', channel: 'sdk', sdkVersion: '1.4.0', tracingMode: 'no_otel', canRecord: false }] },
  { state: 'non_sdk_fetches', fetches: 4, consumers: [{ label: 'm', channel: 'mcp', canRecord: false }] },
  { state: 'awaiting_loads', consumers: [{ label: 'k', channel: 'sdk', canRecord: true }] },
  { state: 'partially_recording', consumers: [{ label: 'k', channel: 'sdk', canRecord: false }] },
  { state: 'unknown_state', consumers: [] },
];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  setSkillTracesOverride(true);
  hooks.runtime.mockReturnValue(success(RUNTIME));
  hooks.comparison.mockReturnValue(success(MOVE));
  hooks.series.mockReturnValue(success({ series: [] }));
  hooks.agent.mockReturnValue(success({ agent: null, rows: ROWS.filter((row) => row.agent).map((row) => ({ ...row, skillId: 'skill-a', skillName: 'refund-policy' })) }));
  hooks.context.mockReturnValue(success({ ...RUNTIME, rows: ROWS.map((row) => ({ ...row, skillId: 'skill-a', skillName: 'refund-policy' })), linkedNotLoaded: [{ skillId: 'b', name: 'tone', linkedVersion: 1 }], loadedNotLinked: [], move: { eventId: 'e' } }));
  hooks.contextComparison.mockReturnValue(success({ ...MOVE, changedSkills: [{ skillId: 'a', name: 'x', fromVersion: 1, toVersion: 2, change: 'changed' }] }));
  hooks.latest.mockReturnValue(success({ load: null, fetch: { sdkVersion: '0.9.2', client: 'futureagi-python', tracingMode: 'off', canRecord: false } }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setSkillTracesOverride(null);
});

describe('runtime copy (§2.1, UX-06)', () => {
  it('no exported string or sample sentence carries a retired word', () => {
    const samples = [
      ...Object.values(copy).filter((value) => typeof value === 'string'),
      ...Object.values(copy.EVIDENCE), ...Object.values(copy.METRIC), ...copy.ABOUT_LINES, ...Object.values(copy.DIAGNOSIS_ACTIONS),
      ...Object.values(copy.TRACING_FIX), ...Object.values(copy.VERDICT_TEXT),
      ...Object.values(copy.DIAGNOSIS).filter((value) => typeof value === 'string'),
      copy.DIAGNOSIS.neverFetched('skill'),
      copy.DIAGNOSIS.olderSdk({ count: 3, kind: 'skill', days: 7, minVersion: '1.4.0' }),
      copy.DIAGNOSIS.tracingUnavailable({ count: 3, kind: 'skill', days: 7, off: 2, noOtel: 1 }),
      copy.DIAGNOSIS.nonSdk({ fetches: 212, channels: 'MCP and download' }),
      copy.DIAGNOSIS.partial({ agents: 2, others: 3, kind: 'skill' }),
      copy.leadSentence({ subject: 'v4', agents: 6, projects: 3, compare: { agents: 5, metric: 'failed_requests', aText: '2.1%', bText: '0.8%', versionA: 4, versionB: 3 } }),
      copy.moveChipText({ verdict: 'worse_so_far', toVersion: 2, metric: 'failed_requests', agents: 5, hoursElapsed: 6, hoursTotal: 24 }),
      ...['got_new', 'asked_for_version', 'pinned_in_context', 'waiting_for_context_release', 'got_old_after_move', 'not_seen'].map((reason) => copy.notMovedText({ reason, fromVersion: 3, toVersion: 4, contextName: 'support-agent', cached: true })),
      copy.failedLoadsSentence({ total: 14, name: 'refund-policy', windowValue: '24h', byErrorType: { integrity: 12, authorization: 2 } }),
      copy.mismatchSentence(31),
      copy.receiptLoads({ agents: 4, verdictText: 'Watching, 18 h left' }),
      copy.lastFetchReason({ sdkVersion: '0.9.2', language: 'futureagi-python', tracingMode: 'on', minVersion: '1.4.0' }),
    ];
    samples.forEach((text) => expect(forbiddenIn(text), text).toEqual([]));
  });

  it('the forbidden list covers every §9.1 word', () => {
    ['Used (7 days)', 'using v3', 'in use', 'Error rate', 'In scope', 'unrecognised digest', 'Unused', '0 agents'].forEach((text) => expect(copy.forbiddenRuntimeWords(text), text).not.toEqual([]));
    expect(copy.forbiddenRuntimeWords('Loaded by 3 agents · 10 agents · Ran after loading')).toEqual([]);
  });

  it('uses the §2.1 words', () => {
    expect(copy.EVIDENCE).toEqual({ delivered: 'Given to the app', agent_read: 'Read by the agent', declared: 'Reported by the app' });
    expect(copy.RAN_AFTER_LOADING).toBe('Ran after loading');
    expect(copy.FAILED_REQUESTS).toBe('Failed requests');
    expect(copy.CONTENTS_MISMATCH).toBe("Contents don't match the published version");
    expect(copy.askedFor('production')).toBe('Asked for production');
    expect(copy.labelNow('production')).toBe('production now');
    expect([copy.RUNTIME_TAB, copy.LOADED_BY, copy.SKILLS_LOADED, copy.SKILLS_LOADED_AT_RUNTIME, copy.LOADED_7_DAYS, copy.LINKED_CONTEXTS, copy.RETRIEVAL_FETCHES])
      .toEqual(['Runtime', 'Loaded by', 'Skills loaded', 'Skills loaded at runtime', 'Loaded (7 days)', 'Linked contexts', 'Retrieval (fetches)']);
  });

  const cases = [
    ['RuntimeTab', () => h(RuntimeTab, { skill: SKILL, productionVersion: 4 }), '/dashboard/skills/skill-a?tab=runtime&move=event-1'],
    ['ContextRuntimeView', () => h(ContextRuntimeView, { context: { id: 'ctx-1', name: 'support-agent' } })],
    ['LoadedByTable', () => h(LoadedByTable, { skill: SKILL, rows: ROWS, productionVersion: 4, allVersions: [{ id: 'v1', number: 1 }] })],
    ['MoveCardBody', () => h(MoveCardBody, { data: MOVE, subjectId: SKILL.id })],
    ['ContextReleaseCard', () => h(ContextReleaseCard, { contextId: 'ctx-1', eventId: 'e' })],
    ['MoveStateChip', () => h(MoveStateChip, { move: MOVE, skillId: SKILL.id, now: NOW })],
    ['AgentPanel', () => h(AgentPanel, { open: true, onClose: () => {}, agent: agentPanelInput(ROWS[2].agent) })],
    ['FailedLoadsLine', () => h('div', null, h(FailedLoadsLine, { failedLoads: RUNTIME.failedLoads, name: 'refund-policy', windowValue: '24h' }), h(ContentsWarningLine, { mismatch: RUNTIME.mismatch }))],
    ['RuntimeNotes', () => h(RuntimeNotes, { coverage: { projectsScanned: 50, projectsTotal: 63, unreadableProjects: 2 }, truncated: true, incomplete: 3, noEvals: true })],
    ['LinkedLoadsCell', () => h('div', null,
      h(LinkedLoadsCell, { contextId: 'ctx-1', entry: { skillId: 'a', linkedVersion: 2, traces7d: 1204, loadedVersions: [2] } }),
      h(LinkedLoadsCell, { contextId: 'ctx-1', entry: { skillId: 'a', linkedVersion: 2, traces7d: 0, loadedVersions: [] } }),
      h(LinkedLoadsCell, { contextId: 'ctx-1', entry: { skillId: 'a', linkedVersion: 2, traces7d: 9, loadedVersions: [1] } }),
      h(LoadedNotLinkedList, { items: [{ skillId: 'z', name: 'extra', version: 1, traces: 3 }] }),
      h(LibraryLoadsCell, { runtime: { loaded7d: null, canRecord: false, move: { verdict: 'watching', toVersion: 4 } } }),
      h(LibraryLoadsCell, { runtime: { loaded7d: 0, canRecord: true, move: { verdict: 'worse', toVersion: 4, metric: 'failed_requests' } } }))],
    ['ReceiptLoadsLine', () => h(ReceiptLoadsLine, { subjectId: SKILL.id, eventId: 'event-1' })],
  ];

  it.each(cases)('%s renders no retired word', (_name, element, route) => {
    renderRuntime(element(), route ? { route } : undefined);
    expect(forbiddenIn(rendered())).toEqual([]);
  });

  it.each(DIAGNOSES)('RuntimeDiagnosis $state renders no retired word', (diagnosis) => {
    renderRuntime(h(RuntimeDiagnosis, { empty: true, diagnosis, subjectId: SKILL.id }));
    fireEvent.click(screen.queryByRole('button', { name: 'See which' }) || document.body);
    expect(forbiddenIn(rendered())).toEqual([]);
  });

  it('CheckTracing renders no retired word while waiting and after a reason', () => {
    renderRuntime(h(CheckTracing, { id: SKILL.id, name: SKILL.name, now: NOW }));
    fireEvent.click(screen.getByRole('button', { name: 'Check tracing' }));
    expect(forbiddenIn(rendered())).toEqual([]);
  });

  it('library and linked cells say what they mean', () => {
    renderRuntime(cases.find(([name]) => name === 'LinkedLoadsCell')[1]());
    expect(screen.getByText('1,204 traces')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'No loads recorded' })).toHaveAttribute('href', '/dashboard/skills/contexts/ctx-1?tab=runtime');
    expect(screen.getByText('Loaded as v1; this context links v2')).toBeInTheDocument();
    expect(screen.getByLabelText("Can't record loads yet")).toHaveTextContent('—');
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('v4 · watching')).toBeInTheDocument();
    expect(screen.getByText('v4 · failed requests up')).toBeInTheDocument();
    expect(screen.getByText('Loaded but not linked here')).toBeInTheDocument();
  });

  it('the receipt line reports loads since the move and links to the card', () => {
    renderRuntime(h(ReceiptLoadsLine, { subjectId: SKILL.id, eventId: 'event-1' }));
    expect(screen.getByRole('link', { name: 'Loaded by 2 agents since the move · Worse so far after the move' }))
      .toHaveAttribute('href', '/dashboard/skills/skill-a?tab=runtime&move=event-1&window=24h');
  });

  it('no runtime component opens a new tab', () => {
    renderRuntime(cases[0][1](), { route: cases[0][2] });
    document.querySelectorAll('a').forEach((anchor) => expect(anchor).not.toHaveAttribute('target'));
  });
});
