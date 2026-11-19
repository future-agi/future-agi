import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { useCreateSkillAssist, usePreviewSkillAssist, useApplySkillAssist, useSkillAssist } from './assist';

vi.mock('src/utils/axios', () => ({ default: { post: vi.fn() } }));
vi.mock('./endpoints', () => ({ endpoints: { assist: () => '/assist/', assistPreview: () => '/assist/preview/', applyAssist: () => '/assist/proposal/apply/', assistProposal: () => '/assist/proposal/' } }));
vi.mock('./testing', () => ({ useSkillV11Mutation: (options) => options, useSkillV11Query: (options) => options, idempotencyHeaders: (key) => ({ 'Idempotency-Key': key }), skillV11Result: (response) => response.data.result }));
const input = { kind: 'draft', revision: 3, expectedDigest: 'digest', instruction: 'Use approved policy.', contextText: 'Synthetic policy', model: { modelId: 'fixture', temperature: 0, maxOutputTokens: 200 }, maxEstimatedCostUsd: '0.50000000' };
const config = { headers: { 'X-Workspace-Id': 'workspace' } };
beforeEach(() => vi.clearAllMocks());
describe('assistance preflight', () => {
  it('previews non-paid input without attaching spend consent', async () => {
    axios.post.mockResolvedValue({ data: { result: { input_fingerprint: 'fp', provider_disclosure: {}, allowance: {}, estimate: { upper_bound_usd: '0.2', pricing_source: 'fixture' }, outbound_findings: [] } } });
    await usePreviewSkillAssist('skill').send(input, config);
    expect(axios.post.mock.calls[0]).toEqual(['/assist/preview/', { kind: 'draft', revision: 3, expected_digest: 'digest', instruction: 'Use approved policy.', context_text: 'Synthetic policy', model: { model_id: 'fixture', temperature: 0, max_output_tokens: 200 }, max_estimated_cost_usd: '0.50000000' }, config]);
  });
  it('binds creation to exact reviewed finding IDs and explicit consent', async () => {
    axios.post.mockResolvedValue({ data: { result: { id: 'proposal', status: 'queued', poll_after_ms: 2000, deadline_at: 'date', kind: 'draft', base_revision: 3, base_digest: 'digest' } } });
    await useCreateSkillAssist('skill').send({ ...input, idempotencyKey: 'key', expectedInputFingerprint: 'fp', acknowledgeProviderProcessing: true, outboundReview: { fingerprint: 'fp', falsePositiveSecretFindings: [{ findingId: 'finding', reason: 'Synthetic fixture' }] } }, config);
    expect(axios.post.mock.calls[0][1]).toMatchObject({ expected_input_fingerprint: 'fp', acknowledge_provider_processing: true, outbound_review: { fingerprint: 'fp', false_positive_secret_findings: [{ finding_id: 'finding', reason: 'Synthetic fixture' }] } });
    expect(axios.post.mock.calls[0][2].headers).toEqual({ 'X-Workspace-Id': 'workspace', 'Idempotency-Key': 'key' });
  });
  it('applies server-side using canonical revision, never browser-authored proposal files', async () => {
    axios.post.mockResolvedValue({ data: { result: { revision: 4, base_version: 1, files: [] } } });
    const result = await useApplySkillAssist('skill', 'proposal').send({ revision: 3, expectedDigest: 'digest', files: ['not sent'] }, config);
    expect(axios.post.mock.calls[0][1]).toEqual({ revision: 3, expected_digest: 'digest' });
    expect(result.revision).toBe(4);
  });
  it('polls proposal summaries only while the shared poller considers them active', () => {
    expect(useSkillAssist('skill', 'proposal')).toMatchObject({ poll: true, parts: ['skill', 'assist', 'proposal'] });
  });
});
