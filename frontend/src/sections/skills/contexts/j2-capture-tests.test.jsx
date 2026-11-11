import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextTraceCaptureAction from './ContextTraceCaptureAction';
import { physicalCaptureRef } from './physical-capture-ref';
import ContextTestsPanel from './ContextTestsPanel';

const idle = vi.hoisted(() => () => ({ mutate: () => {}, mutateAsync: async () => {}, reset: () => {}, isPending: false }));
const state = vi.hoisted(() => ({ identity: 'scope-a', canPublish: true, suiteError: false, runtime: false, capture: vi.fn() }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ enabled: true, identity: state.identity }) }));
vi.mock('src/api/skills/context-authoring', () => ({
  useContexts: () => ({ data: { totalPages: 1, results: [{ id: 'context', name: 'Refund policy' }] } }),
  useContextDetail: () => ({ data: { releaseState: { permissions: { canPublish: state.canPublish } } } }),
}));
vi.mock('src/api/skills/context-testing', () => ({
  useContextTestSuite: () => ({ data: state.suiteError ? undefined : { revision: 3, cases: [] }, isError: state.suiteError, refetch: vi.fn() }),
  useSaveContextTestSuite: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock('src/api/skills/context-native', () => ({
  useNativeTargets: () => ({ data: { runtime: { available: state.runtime, runtime: 'test-runtime', evidenceClass: 'synthetic fixture' }, targets: [] } }),
  useNativeRuns: () => ({ data: [] }), useNativeRun: () => ({ data: undefined }), useRuntimeConfiguration: () => ({ data: undefined }),
  useCreateNativeTarget: idle, useQualifyNativeTarget: idle, usePreviewNativeRun: idle, useCreateNativeRun: idle,
}));
vi.mock('src/api/skills/evidence-annotations', () => ({ useEvidenceReference: () => ({}), useEvidenceAnnotations: () => ({}), useAppendEvidenceAnnotation: idle }));
vi.mock('./ContextCaptureController', () => ({ default: (props) => { state.capture(props); return <div>Source review</div>; } }));
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
  fireEvent.click(screen.getByRole('button', { name: 'Create context regression case' }));
  fireEvent.mouseDown(screen.getByLabelText('Regression context'));
  fireEvent.click(screen.getByRole('option', { name: 'Refund policy' }));
  fireEvent.click(screen.getByRole('button', { name: 'Review selected span' }));
  expect(state.capture).toHaveBeenCalledWith(expect.objectContaining({ contextId: 'context', traceRef: ref, canCapture: true }));
  expect(screen.queryByRole('textbox', { name: 'Source project ID' })).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'Span start time' })).not.toBeInTheDocument();
});
it('drops an open source selection when the authenticated workspace changes', () => {
  const view = render(<ContextTraceCaptureAction traceRef={ref} />);
  fireEvent.click(screen.getByRole('button', { name: 'Create context regression case' }));
  expect(screen.getByRole('dialog')).toBeVisible();
  state.identity = 'scope-b';
  view.rerender(<ContextTraceCaptureAction traceRef={ref} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('does not grant capture rights from a context list entry alone', () => {
  state.canPublish = false;
  render(<ContextTraceCaptureAction traceRef={ref} />);
  fireEvent.click(screen.getByRole('button', { name: 'Create context regression case' }));
  fireEvent.mouseDown(screen.getByLabelText('Regression context'));
  fireEvent.click(screen.getByRole('option', { name: 'Refund policy' }));
  expect(screen.getByRole('button', { name: 'Review selected span' })).toBeDisabled();
  expect(state.capture).not.toHaveBeenCalled();
});
it('renders context cases while clearly withholding native reproduction and release claims', () => {
  render(<ContextTestsPanel contextId="context" version={{ number: 4 }} canWrite />);
  expect(screen.getByText('Context regression cases · viewed v4')).toBeVisible();
  expect(screen.getByText(/Application verification unavailable/)).toBeVisible();
  expect(screen.getByText('No regression cases yet')).toBeVisible();
});
it('offers application verification only when the deployment has a hosted runtime', () => {
  state.runtime = true;
  render(<ContextTestsPanel contextId="context" version={{ number: 4 }} versions={[{ number: 4 }]} canWrite />);
  expect(screen.queryByText(/Application verification unavailable/)).not.toBeInTheDocument();
  expect(screen.getByText('Application target')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add application target' })).toBeEnabled();
  expect(screen.getByText(/synthetic fixture/)).toBeVisible();
});
it('does not turn an inaccessible suite into empty successful coverage', () => {
  state.suiteError = true;
  render(<ContextTestsPanel contextId="context" canWrite />);
  expect(screen.getByText('Could not load context regression cases')).toBeVisible();
  expect(screen.queryByText('No regression cases yet')).not.toBeInTheDocument();
});
