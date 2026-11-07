export const mapActor = (value) => value ? { id: value.id, name: value.name, ...(value.kind ? { kind: value.kind } : {}) } : null;
export const mapFile = (value) => ({
  path: value.path, sizeBytes: value.size_bytes, sha256: value.sha256,
  ...(value.token_estimate !== undefined ? { tokenEstimate: value.token_estimate } : {}),
  ...(value.content !== undefined ? { content: value.content } : {}),
});
export const mapLabel = (value) => ({ name: value.name, version: value.version, versionId: value.version_id, updatedAt: value.updated_at, updatedBy: mapActor(value.updated_by), isProtected: value.is_protected, canMove: value.can_move, canRemove: value.can_remove, releaseRevision: value.release_revision });
export const mapChecks = (value) => value ? {
  items: value.items || [], summary: value.summary || { errors: 0, warnings: 0, infos: 0 },
  tokens: { metadata: value.tokens?.metadata || 0, skillMd: value.tokens?.skill_md || 0, total: value.tokens?.total || 0 },
  frontmatter: value.frontmatter,
} : null;
export const mapVersion = (value) => value ? {
  id: value.id, number: value.number, digest: value.digest, note: value.note,
  createdAt: value.created_at, createdBy: mapActor(value.created_by),
  fileCount: value.file_count, totalBytes: value.total_bytes,
  labels: value.labels || [], source: value.source,
  ...(value.frontmatter !== undefined ? { frontmatter: value.frontmatter } : {}),
  ...(value.checks !== undefined ? { checks: mapChecks(value.checks) } : {}),
  ...(value.files !== undefined ? { files: value.files.map(mapFile) } : {}),
} : null;
export const mapSkill = (value) => value ? {
  id: value.id, name: value.name, description: value.description, tags: value.tags || [],
  releaseRevision: value.release_revision, protectedLabelNames: value.protected_label_names || [],
  ...(value.permissions ? { permissions: { canManageLabelProtection: value.permissions.can_manage_label_protection, canArchive: value.permissions.can_archive, canRunTests: value.permissions.can_run_tests } } : {}),
  latestVersion: value.latest_version, labels: (value.labels || []).map(mapLabel),
  hasDraft: value.has_draft, updatedAt: value.updated_at, updatedBy: mapActor(value.updated_by),
  createdAt: value.created_at, fetches7d: value.fetches_7d, fileCount: value.file_count,
  ...(value.source !== undefined ? { source: value.source } : {}),
  ...(value.created_by !== undefined ? { createdBy: mapActor(value.created_by) } : {}),
  ...(value.latest !== undefined ? { latest: mapVersion(value.latest) } : {}),
  ...(value.drafts !== undefined ? { drafts: value.drafts.map((draft) => ({ user: mapActor(draft.user), updatedAt: draft.updated_at, baseVersion: draft.base_version, isMine: draft.is_mine })) } : {}),
} : null;
export const mapPage = (value, mapper) => ({
  results: (value.results || []).map(mapper), count: value.count, page: value.page,
  pageSize: value.page_size, totalPages: value.total_pages,
  ...(value.stats ? { stats: { total: value.stats.total, inProduction: value.stats.in_production, fetches7d: value.stats.fetches_7d, myDrafts: value.stats.my_drafts } } : {}),
});
export const mapDraft = (value) => value ? { baseVersion: value.base_version, files: value.files, note: value.note, revision: value.revision, updatedAt: value.updated_at, source: value.source } : null;
export const mapTemplate = (value) => ({ id: value.id, name: value.name, title: value.title, description: value.description, tags: value.tags || [], files: (value.files || []).map(mapFile) });
export const mapCompare = (value) => ({
  from: value.from, to: value.to, summary: value.summary,
  files: (value.files || []).map((file) => ({ path: file.path, status: file.status, fromSha256: file.from_sha256, toSha256: file.to_sha256, ...(file.from_text !== undefined ? { fromText: file.from_text } : {}), ...(file.to_text !== undefined ? { toText: file.to_text } : {}) })),
});
export const mapLabelEvent = (value) => ({ label: value.label, fromVersion: value.from_version, toVersion: value.to_version, actor: mapActor(value.actor), releaseRevision: value.release_revision, note: value.note, createdAt: value.created_at });
export const mapUsage = (value) => ({
  days: value.days, total: value.total, byDay: value.by_day || [], byChannel: value.by_channel,
  byVersion: (value.by_version || []).map(({ version, count, last_at }) => ({ version, count, lastAt: last_at })),
  consumers: (value.consumers || []).map(({ label, channel, count, last_at }) => ({ label, channel, count, lastAt: last_at })),
  lastFetchedAt: value.last_fetched_at,
});
export const mapPublish = (value) => ({ releaseRevision: value.release_revision, evidence: value.evidence ? { runId: value.evidence.run_id, validity: value.evidence.validity, currentness: { content: value.evidence.currentness.content, baseline: value.evidence.currentness.baseline, suite: value.evidence.currentness.suite, competitors: value.evidence.currentness.competitors, payloadAvailable: value.evidence.currentness.payload_available } } : null, version: mapVersion(value.version), unchanged: value.unchanged, checks: mapChecks(value.checks), ...(value.skill ? { skill: mapSkill(value.skill) } : {}) });
const mapImportBundle = (value) => ({ key: value.key, root: value.root, name: value.name, fileCount: value.file_count, totalBytes: value.total_bytes, status: value.status, skillId: value.skill_id, latestVersion: value.latest_version, checks: mapChecks(value.checks) });
export const mapImportPreview = (value) => ({ bundles: (value.bundles || []).map(mapImportBundle), rejected: value.rejected || [], skipped: value.skipped || [] });
const mapImported = (value) => ({ ...value, ...(value.skill_id !== undefined ? { skillId: value.skill_id } : {}), ...(value.version_id !== undefined ? { versionId: value.version_id } : {}) });
export const mapImportResult = (value) => ({ created: (value.created || []).map(mapImported), updated: (value.updated || []).map(mapImported), unchanged: (value.unchanged || []).map(mapImported), failed: value.failed || [] });
