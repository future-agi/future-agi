import { describe, expect, it } from 'vitest';
import { mapSkill, mapVersion, mapPage, mapChecks, mapDraft, mapCompare, mapLabelEvent, mapUsage, mapImportPreview, mapImportResult, mapPublish } from './mappers';

const actor = { id: 'user-1', name: 'Reviewer' };
const version = { id: 'version-1', number: 1, file_count: 2, total_bytes: 123, created_by: actor, created_at: '2026-11-06', labels: ['production'], files: [{ path: 'SKILL.md', size_bytes: 123, token_estimate: 30 }] };
describe('skills wire mappers', () => {
  it('maps detail fields and other users drafts explicitly', () => {
    expect(mapSkill({ id: 'skill', latest_version: 1, has_draft: true, fetches_7d: 7, file_count: 2, updated_by: actor, latest: version, drafts: [{ user: actor, updated_at: 'today', base_version: 1, is_mine: false }] })).toMatchObject({ latestVersion: 1, hasDraft: true, fetches7d: 7, fileCount: 2, updatedBy: actor, latest: { number: 1 }, drafts: [{ baseVersion: 1, isMine: false, updatedAt: 'today' }] });
  });
  it('maps workspace-wide stats independently of the result page', () => {
    expect(mapPage({ results: [], count: 0, page: 1, page_size: 25, total_pages: 0, stats: { total: 10, in_production: 8, fetches_7d: 100, my_drafts: 2 } }, mapSkill)).toEqual({ results: [], count: 0, page: 1, pageSize: 25, totalPages: 0, stats: { total: 10, inProduction: 8, fetches7d: 100, myDrafts: 2 } });
  });
  it('maps immutable file metadata, frontmatter and check tokens', () => {
    const checks = { items: [], tokens: { skill_md: 30, metadata: 1, total: 31 }, frontmatter: { 'allowed-tools': 'Read', metadata: { snake_key: 'preserved' } } };
    const mapped = mapVersion({ ...version, checks, frontmatter: checks.frontmatter });
    expect(mapped.files[0]).toMatchObject({ sizeBytes: 123, tokenEstimate: 30 });
    expect(mapped.checks.tokens).toEqual({ skillMd: 30, metadata: 1, total: 31 });
    expect(mapped.frontmatter).toEqual(checks.frontmatter);
    expect(mapChecks(null)).toBeNull();
  });
  it('preserves draft contents and revision', () => expect(mapDraft({ base_version: null, revision: 2, files: [{ path: 'SKILL.md', content: 'text' }], updated_at: 'today' })).toMatchObject({ baseVersion: null, revision: 2, files: [{ path: 'SKILL.md', content: 'text' }], updatedAt: 'today' }));
  it('maps optional comparison texts without creating missing fields', () => {
    const mapped = mapCompare({ from: 1, to: 2, files: [{ path: 'a', status: 'removed', from_text: 'old', from_sha256: 'digest' }, { path: 'b', status: 'unchanged' }] });
    expect(mapped.files[0]).toMatchObject({ fromText: 'old', fromSha256: 'digest' });
    expect(mapped.files[1]).not.toHaveProperty('toText');
  });
  it('maps label history and usage timestamps', () => {
    expect(mapLabelEvent({ label: 'production', from_version: 1, to_version: null, actor: { ...actor, kind: 'api_key' } })).toMatchObject({ fromVersion: 1, toVersion: null, actor: { kind: 'api_key' } });
    expect(mapUsage({ by_version: [{ version: 1, count: 2, last_at: 'today' }], consumers: [{ label: 'CI', last_at: 'today' }], last_fetched_at: 'today' })).toMatchObject({ byVersion: [{ lastAt: 'today' }], consumers: [{ lastAt: 'today' }], lastFetchedAt: 'today' });
  });
  it('maps import and publish results', () => {
    expect(mapImportPreview({ bundles: [{ key: 'one', file_count: 2, total_bytes: 123, skill_id: 'id', latest_version: 1 }] }).bundles[0]).toMatchObject({ fileCount: 2, totalBytes: 123, skillId: 'id', latestVersion: 1 });
    expect(mapImportResult({ created: [{ skill_id: 'id', version_id: 'v1' }] }).created[0]).toMatchObject({ skillId: 'id', versionId: 'v1' });
    expect(mapPublish({ version, unchanged: true })).toMatchObject({ unchanged: true, version: { fileCount: 2 } });
  });
});

it('preserves authoritative permissions, aggregate revisions and evidence on release responses', () => {
  const mapped = mapSkill({ id: 'unpublished', release_revision: 0, protected_label_names: ['production'], labels: [], permissions: { can_manage_label_protection: false, can_archive: true, can_run_tests: false } });
  expect(mapped).toMatchObject({ releaseRevision: 0, protectedLabelNames: ['production'], permissions: { canManageLabelProtection: false, canArchive: true, canRunTests: false } });
  const currentness = { content: true, baseline: false, suite: true, competitors: null, payload_available: true };
  expect(mapPublish({ version, unchanged: true, release_revision: 8, evidence: { run_id: 'run', validity: 'diagnostic', currentness } })).toMatchObject({ releaseRevision: 8, evidence: { runId: 'run', validity: 'diagnostic', currentness: { baseline: false, payloadAvailable: true } } });
  const source = { provider: 'github', commit_sha: 'immutable', local_modifications: true };
  expect(mapDraft({ files: [], revision: 2, source }).source).toEqual(source);
});
