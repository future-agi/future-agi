import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextTraceCaptureAction from './ContextTraceCaptureAction';
import { contextHintFrom, physicalCaptureRef } from './physical-capture-ref';
import ContextTestsPanel from './ContextTestsPanel';

const idle = vi.hoisted(() => () => ({ mutate: () => {}, mutateAsync: async () => {}, reset: () => {}, isPending: false }));
const state = vi.hoisted(() => ({ identity: 'scope-a', canPublish: true, suiteError: false, runtime: false, capture: vi.fn() }));
vi.mock('src/api/skills/r3-spend', () => ({ useSkillsSpendPolicy: () => ({}) }));
vi.mock('src/api/skills/testing', () => ({
  useSkillV11Scope: () => ({ enabled: true, identity: state.identity }),
  // The unified Tests panel also mounts the whole-context section; it stays idle here.
  useSkillTestingOptions: () => ({ data: { enabled: true, models: [], limits: { profiles: { quick: { maxTotalTokens: 1000 } } } } }),
  isActiveSkillJob: () => false,
}));
vi.mock('src/api/skills/context-authoring', () => ({
  useContexts: () => ({ data: { totalPages: 1, results: [{ id: 'context', name: 'Refund policy' }] } }),
  useContextDetail: () => ({ data: { releaseState: { permissions: { canPublish: state.canPublish } } } }),
}));
vi.mock('src/api/skills/context-testing', () => ({
  useContextTestSuite: () => ({ data: state.suiteError ? undefined : { revision: 3, cases: [] }, isError: state.suiteError, refetch: vi.fn() }),
  useSaveContextTestSuite: () => ({ mutateAsync: vi.fn() }),
  contextTestCandidate: (draft) => ({ kind: 'draft', revision: draft.revision, digest: draft.digest }),
  usePreviewContextTest: idle, useCreateContextTest: idle, useCancelContextTest: idle,
  useContextTestRuns: () => ({ data: { results: [] } }), useContextTestRun: () => ({}), useContextTestResults: () => ({}), useContextTestAttempt: () => ({}),
  useNativeTargets: () => ({ data: { runtime: { available: state.runtime, runtime: 'test-runtime', evidenceClass: 'synthetic fixture' }, targets: [] } }),
  useNativeRuns: () => ({ data: [] }), useNativeRun: () => ({ data: undefined }), useRuntimeConfiguration: () => ({ data: undefined }),
  useCreateNativeTarget: idle, usePrepareReleasePacket: idle, useQualifyNativeTarget: idle, usePreviewNativeRun: idle, useCreateNativeRun: idle,
}));
vi.mock('src/api/skills/evidence-annotations', () => ({ useEvidenceReference: () => ({}), useAllEvidenceAnnotations: () => ({}), useAppendEvidenceAnnotation: idle }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ user: { id: 'user' } }) }));
vi.mock('./ContextCaptureController', () => ({ default: (props) => { state.capture(props); return <button type="button" onClick={props.onChangeContext}>Change context</button>; } }));
const projectId = '10000000-0000-4000-8000-000000000001';
const span = { trace: 'trace', id: 'span', start_time: '2026-11-10T00:00:00.123456Z' };
const ref = { projectId, traceId: span.trace, spanId: span.id, startTime: span.start_time };
afterEach(cleanup);
beforeEach(() => { state.identity = 'scope-a'; state.canPublish = true; state.suiteError = false; state.runtime = false; state.capture.mockClear(); });

it('carries the exact physical identity without truncating sub-millisecond precision', () => {
  expect(physicalCaptureRef(projectId, span)).toEqual(ref);
  expect(physicalCaptureRef(projectId, { ...span, start_time: '2026-11-10T00:00:00' })).toBeNull();
  expect(physicalCaptureRef(projectId, { ...span, id: undefined })).toBeNull();
  expect(physicalCaptureRef('missing-project', span)).toBeNull();
});
it('selects a destination and passes all captured identities without manual fields', () => {
  render(<ContextTraceCaptureAction traceRef={ref} />);
  fireEvent.click(screen.getByRole('button', { name: 'Save as test case' }));
  fireEvent.mouseDown(screen.getByLabelText('Context'));
  fireEvent.click(screen.getByRole('option', { name: 'Refund policy' }));
  fireEvent.click(screen.getByRole('button', { name: 'Review selected span' }));
  expect(state.capture).toHaveBeenCalledWith(expect.objectContaining({ contextId: 'context', traceRef: ref, canCapture: true }));
  expect(screen.queryByRole('textbox', { name: 'Source project ID' })).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'Span start time' })).not.toBeInTheDocument();
});
it('opens the review directly in the context the span was delivered from, keeping every identity', () => {
  render(<ContextTraceCaptureAction traceRef={ref} contextHint="context" />);
  fireEvent.click(screen.getByRole('button', { name: 'Save as test case' }));
  expect(state.capture).toHaveBeenCalledWith(expect.objectContaining({ contextId: 'context', traceRef: ref, canCapture: true }));
  expect(screen.queryByLabelText('Context')).not.toBeInTheDocument();
});
it('lets the reviewer leave the delivered context for another one', () => {
  render(<ContextTraceCaptureAction traceRef={ref} contextHint="context" />);
  fireEvent.click(screen.getByRole('button', { name: 'Save as test case' }));
  fireEvent.click(screen.getByRole('button', { name: 'Change context' }));
  expect(screen.getByLabelText('Context')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Review selected span' })).toBeDisabled();
});
it('falls back to choosing a context when the delivered context cannot be captured into', () => {
  state.canPublish = false;
  render(<ContextTraceCaptureAction traceRef={ref} contextHint="context" />);
  fireEvent.click(screen.getByRole('button', { name: 'Save as test case' }));
  expect(state.capture).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Context')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Review selected span' })).toBeDisabled();
});
it('reads only a well-formed context id from the span attributes as a default', () => {
  const id = '20000000-0000-4000-8000-000000000002';
  expect(contextHintFrom({ span_attributes: { 'fi.context.id': id } })).toBe(id);
  expect(contextHintFrom({ span_attributes: { 'fi.context.id': 'not-a-uuid' } })).toBeUndefined();
  expect(contextHintFrom({})).toBeUndefined();
});
it('drops an open source selection when the authenticated workspace changes', () => {
  const view = render(<ContextTraceCaptureAction traceRef={ref} />);
  fireEvent.click(screen.getByRole('button', { name: 'Save as test case' }));
  expect(screen.getByRole('dialog')).toBeVisible();
  state.identity = 'scope-b';
  view.rerender(<ContextTraceCaptureAction traceRef={ref} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('does not grant capture rights from a context list entry alone', () => {
  state.canPublish = false;
  render(<ContextTraceCaptureAction traceRef={ref} />);
  fireEvent.click(screen.getByRole('button', { name: 'Save as test case' }));
  fireEvent.mouseDown(screen.getByLabelText('Context'));
  fireEvent.click(screen.getByRole('option', { name: 'Refund policy' }));
  expect(screen.getByRole('button', { name: 'Review selected span' })).toBeDisabled();
  expect(state.capture).not.toHaveBeenCalled();
});
it('renders context cases while clearly withholding native reproduction and release claims', () => {
  render(<MemoryRouter><ContextTestsPanel contextId="context" version={{ number: 4 }} canWrite /></MemoryRouter>);
  expect(screen.getByText('Test cases')).toBeVisible();
  fireEvent.click(screen.getByRole('radio', { name: /^Test on the application/ }));
  expect(screen.getByText('Application tests are off')).toBeVisible();
  expect(screen.queryByText(/Application verification is off/)).not.toBeInTheDocument();
  expect(screen.getByText('No test cases yet')).toBeVisible();
});
it('offers the application test only when the deployment has a hosted runtime', () => {
  state.runtime = true;
  render(<MemoryRouter><ContextTestsPanel contextId="context" version={{ number: 4 }} versions={[{ number: 4 }]} canWrite /></MemoryRouter>);
  expect(screen.queryByText(/Application tests are off/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', { name: /^Test on the application/ }));
  expect(screen.getByText('Application target')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add application target' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'App version info' }));
  expect(screen.getByText(/synthetic fixture/)).toBeVisible();
});
it('does not turn an inaccessible suite into empty successful coverage', () => {
  state.suiteError = true;
  render(<MemoryRouter><ContextTestsPanel contextId="context" canWrite /></MemoryRouter>);
  expect(screen.getByText("Couldn't load test cases")).toBeVisible();
  expect(screen.queryByText('No test cases yet')).not.toBeInTheDocument();
});
