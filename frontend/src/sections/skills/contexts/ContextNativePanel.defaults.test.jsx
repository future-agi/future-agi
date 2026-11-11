import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContextNativePanel from './ContextNativePanel';

const h = vi.hoisted(() => ({ preview: { data: undefined, plans: [] }, create: vi.fn(), runs: [], detail: undefined }));
vi.mock('src/api/skills/context-testing', () => ({
  useNativeTargets: () => ({ data: { runtime: { available: true, runtime: 'container', evidenceClass: 'synthetic fixture' }, targets: [{ bindingId: 'binding', name: 'support-app', revision: 2, qualified: true, configurationDigest: 'sha256:abc' }] } }),
  useNativeRuns: () => ({ data: h.runs }),
  useNativeRun: (_, id) => ({ data: id ? h.detail : undefined, isLoading: false }),
  usePreviewNativeRun: () => ({
    get data() { return h.preview.data; }, isPending: false, isError: false, reset: () => { h.preview.data = undefined; },
    mutate: (plan, options) => { h.preview.plans.push(plan); h.preview.data = { executionFingerprint: 'sha256:plan', reproducible: true, missingInputs: [], cells: 6, ceilingUsd: 1.5, arms: [{ variant: 'candidate', version: plan.candidateVersion }] }; options?.onSuccess?.(); },
  }),
  useCreateNativeRun: () => ({ mutateAsync: h.create, isPending: false, isError: false }),
  useCreateNativeTarget: () => ({ mutate: () => {}, isPending: false }), useQualifyNativeTarget: () => ({ mutateAsync: async () => {} }),
  useRuntimeConfiguration: () => ({ data: undefined }), usePrepareReleasePacket: () => ({ mutate: () => {}, isPending: false }),
}));
vi.mock('src/api/skills/evidence-annotations', () => ({ useEvidenceReference: () => ({}), useEvidenceAnnotations: () => ({}), useAppendEvidenceAnnotation: () => ({ mutateAsync: async () => {} }) }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ user: { id: 'user' } }) }));
const versions = [7, 6, 4].map((number) => ({ number, id: `v${number}` }));
const cases = [{ id: 'refund', title: 'Original refund failure', reviewed: true }, { id: 'chargeback', title: 'Working chargeback case', reviewed: true }, { id: 'draft', title: 'Unreviewed', reviewed: false }];
const renderPanel = (props = {}) => render(<MemoryRouter><ContextNativePanel contextId="context" versions={versions} viewed={{ number: 7 }} cases={cases} canWrite productionVersion={6} {...props} /></MemoryRouter>);
beforeEach(() => {
  h.preview = { data: undefined, plans: [] }; h.create = vi.fn().mockResolvedValue('evidence');
  h.runs = [{ id: 'previous', arms: [{ variant: 'candidate', version: 6 }, { variant: 'reference', version: 4 }] }];
  h.detail = { id: 'previous', status: 'completed', executionFingerprint: 'sha256:previous', received: 2, planned: 2, arms: h.runs[0].arms, cases: [{ id: 'refund', historical: true }, { id: 'chargeback', historical: false }] };
});
afterEach(cleanup);

it('plans the matched run from stored data and starts it only after the reviewer accepts the paid ceiling', async () => {
  renderPanel();
  const button = await screen.findByRole('button', { name: 'Run on the application' });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'I accept up to $1.5 in provider charges for this run.' }));
  await waitFor(() => expect(button).toBeEnabled());
  expect(h.preview.plans.at(-1)).toMatchObject({ candidateVersion: 7, baselines: { reference: 4, production: 6 }, caseIds: ['refund', 'chargeback'], historicalCaseIds: ['refund'], repetitions: 1 });
  expect(screen.getByRole('checkbox', { name: 'Original refund failure' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Working chargeback case' })).toBeChecked();
  expect(screen.queryByRole('button', { name: 'Preview application run' })).not.toBeInTheDocument();
  fireEvent.click(button);
  await waitFor(() => expect(h.create).toHaveBeenCalledOnce());
  expect(h.create.mock.calls[0][0]).toMatchObject({ acknowledgeSpend: true, executionFingerprint: 'sha256:plan' });
});
it('reproduces only the example just captured against its recorded version', async () => {
  h.runs = [];
  renderPanel({ viewed: { number: 6 }, productionVersion: 6, seed: { caseId: 'refund', reference: 4 } });
  await screen.findByRole('button', { name: 'Run on the application' });
  expect(h.preview.plans.at(-1)).toMatchObject({ candidateVersion: 6, baselines: { reference: 4 }, caseIds: ['refund'], historicalCaseIds: ['refund'] });
  expect(h.preview.plans.at(-1).baselines.production).toBeUndefined();
  expect(screen.getByRole('checkbox', { name: 'Working chargeback case' })).not.toBeChecked();
});
it('re-plans after the reviewer deselects an example and refuses a stale plan', async () => {
  renderPanel();
  await screen.findByRole('button', { name: 'Run on the application' });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Working chargeback case' }));
  await waitFor(() => expect(h.preview.plans.at(-1).caseIds).toEqual(['refund']));
});
it('plans nothing without a recorded reference or production baseline', async () => {
  h.runs = [];
  renderPanel({ productionVersion: undefined });
  expect((await screen.findAllByText('Recorded reference version')).length).toBeGreaterThan(0);
  expect(h.preview.plans).toHaveLength(0);
  expect(screen.queryByRole('button', { name: 'Run on the application' })).not.toBeInTheDocument();
});
