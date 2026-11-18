import assert from 'node:assert/strict';
import type { StateProbe } from './state-probe';

/** H0: read-only observations, not a serializer or a copy of attachment business logic.
 * Pinned to models/attachment.py + attachment_storage.py at d949afbc59e0eaadc2fe01a8b245028c90d3cfd3
 * and its model-interface-manifest. This requires the contracted schema; never fall back to Blob.
 * Source presence is NOT evidence that a running database has applied that contract.
 */
export type AttachmentReader = Pick<StateProbe, 'pg' | 'ch'>;
export type AttachmentScope = { organizationId: string; workspaceId: string; projectId: string };

/** Minted identity and pre-upload fixture facts. Never construct this from queried/product rows. */
export type AttachmentSeedFacts = {
  occurrence: string; attachment: string; organization_id: string; workspace_id: string; project_id: string;
  capture_session: string; correlation_nonce: string; writer_kind: 'api_key';
  trace_id: string; span_id: string; direction: string; purpose: string;
  message_index: number | null; part_index: number | null; role: string | null;
  mime_type: string; verified_mime_type: string; verified_byte_length: string; verified_sha256: string;
};

export type AttachmentRow = AttachmentSeedFacts & {
  occurrence_internal_id: string; attachment_internal_id: string; scope_id: string;
  capture_internal_id: string; writer_id: string; bound: boolean; deleted: boolean; state: string;
  storage_binding_id: string; binding_fingerprint: string; verified_blob_id: string;
  verified_blob_created_at: string; verified_storage_key: string; verified_storage_version: string;
  verified_at: string; verified_sample_rate: number | null; verified_channels: number | null;
  verified_frame_count: string | null;
};

export function readAttachmentOccurrences(probe: AttachmentReader, scope: AttachmentScope, occurrences: readonly string[]) {
  assert(occurrences.length > 0, 'an empty occurrence ledger cannot prove attachment readiness');
  assert.equal(new Set(occurrences).size, occurrences.length, 'duplicate expected occurrences');
  return probe.pg<AttachmentRow>(
    `SELECT o.client_id::text AS occurrence, a.client_id::text AS attachment,
            o.id::text AS occurrence_internal_id, a.id::text AS attachment_internal_id,
            s.id::text AS scope_id, s.organization_id::text, s.workspace_id::text, s.project_id::text,
            c.id::text AS capture_internal_id, c.client_id::text AS capture_session,
            c.correlation_nonce::text, c.writer_kind, c.writer_id::text,
            o.trace_id::text, o.span_id, o.direction, o.purpose, o.message_index, o.part_index, o.role,
            o.bound_at IS NOT NULL AS bound, o.deleted_at IS NOT NULL AS deleted,
            a.state, a.mime_type, a.storage_binding_id::text, b.identity_sha256 AS binding_fingerprint,
            a.verified_blob_id::text, a.verified_blob_created_at::text,
            a.verified_storage_key, a.verified_storage_version, a.verified_sha256,
            a.verified_byte_length::text, a.verified_mime_type, a.verified_at::text,
            a.verified_sample_rate, a.verified_channels, a.verified_frame_count::text
       FROM tracer_attachmentoccurrence o
       JOIN tracer_attachmentscope s ON s.id = o.scope_id
       JOIN tracer_attachment a ON a.id = o.attachment_id AND a.scope_id = o.scope_id
       JOIN tracer_capturesession c ON c.id = o.capture_session_id AND c.scope_id = o.scope_id
       LEFT JOIN tracer_attachmentstoragebinding b ON b.id = a.storage_binding_id
      WHERE s.organization_id = $1 AND s.workspace_id = $2 AND s.project_id = $3
        AND o.client_id = ANY($4::uuid[])
      ORDER BY o.client_id`,
    [scope.organizationId, scope.workspaceId, scope.projectId, [...occurrences]]);
}

const sorted = <T extends { occurrence: string }>(rows: readonly T[]) =>
  [...rows].sort((a, b) => a.occurrence.localeCompare(b.occurrence));
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const digest = /^[0-9a-f]{64}$/;

function assertCompleteVerifiedFacts(row: AttachmentRow) {
  for (const field of ['occurrence_internal_id', 'attachment_internal_id', 'scope_id', 'capture_internal_id',
    'writer_id', 'storage_binding_id', 'verified_blob_id'] as const) {
    assert.equal(typeof row[field], 'string', `${field} must be present`);
    assert.match(row[field], uuid, field);
    assert.notEqual(row[field], '00000000-0000-0000-0000-000000000000', field);
  }
  for (const field of ['verified_sha256', 'binding_fingerprint'] as const) {
    assert.equal(typeof row[field], 'string', `${field} must be present`);
    assert.match(row[field], digest, field);
  }
  for (const field of ['verified_storage_key', 'verified_storage_version', 'verified_mime_type'] as const) {
    assert.equal(typeof row[field], 'string', `${field} must be present`);
    assert(row[field].trim().length > 0, `${field} must be nonempty`);
  }
  assert.match(row.verified_byte_length, /^[1-9][0-9]*$/, 'verified length must be positive');
  for (const field of ['verified_blob_created_at', 'verified_at'] as const) {
    assert.equal(typeof row[field], 'string', `${field} must be present`);
    assert(Number.isFinite(Date.parse(row[field])), `${field} must be a timestamp`);
  }
}

export function assertReadyAttachmentOccurrences(rows: readonly AttachmentRow[], expected: readonly AttachmentSeedFacts[]) {
  assert(expected.length > 0, 'an empty seed ledger is not an oracle');
  assert.equal(new Set(expected.map(item => item.occurrence)).size, expected.length, 'duplicate seed identity');
  assert.equal(rows.length, expected.length, 'exact occurrence cardinality');
  const actual = sorted(rows), ledger = sorted(expected);
  actual.forEach((row, index) => {
    const seed = ledger[index];
    // Explicit field projection keeps the expected oracle independent of server-assigned values.
    const facts: AttachmentSeedFacts = {
      occurrence: row.occurrence, attachment: row.attachment,
      organization_id: row.organization_id, workspace_id: row.workspace_id, project_id: row.project_id,
      capture_session: row.capture_session, correlation_nonce: row.correlation_nonce, writer_kind: row.writer_kind,
      trace_id: row.trace_id, span_id: row.span_id, direction: row.direction, purpose: row.purpose,
      message_index: row.message_index, part_index: row.part_index, role: row.role,
      mime_type: row.mime_type, verified_mime_type: row.verified_mime_type,
      verified_byte_length: row.verified_byte_length, verified_sha256: row.verified_sha256,
    };
    assert.deepStrictEqual(facts, seed, `seed facts for ${seed.occurrence}`);
    assert.deepStrictEqual([row.bound, row.deleted, row.state], [true, false, 'ready'], 'bound, live and ready');
    assertCompleteVerifiedFacts(row);
  });
}

/** Preserve the entire observed identity/verified tuple across deletion, not just digest or tombstone.
 * State and deleted are lifecycle observations; no other selected fact may disappear or change.
 * Call only with a pre-action snapshot already checked against the independent seed ledger.
 */
export function assertImmutableAttachmentFacts(before: readonly AttachmentRow[], after: readonly AttachmentRow[]) {
  assert(before.length > 0, 'empty pre-action snapshot');
  assert.equal(new Set(before.map(row => row.occurrence)).size, before.length, 'duplicate pre-action identity');
  for (const row of [...before, ...after]) assertCompleteVerifiedFacts(row);
  const immutable = (rows: readonly AttachmentRow[]) => sorted(rows).map(({ state, deleted, ...facts }) => facts);
  assert.deepStrictEqual(immutable(after), immutable(before), 'complete immutable attachment facts after action');
}

// CH pins: spans is ReplacingMergeTree; trace_id is dashed String, project_id UUID,
// id is the 16-hex source span. No actor/API serializer participates in these reads.
export function readAttachmentRefs(probe: AttachmentReader, project: string, trace: string) {
  return probe.ch<{ id: string; refs: string }>(
    `SELECT id, attrs_string['fi.attachment.refs'] AS refs FROM spans FINAL
      WHERE project_id={p:UUID} AND trace_id={t:String} AND is_deleted=0 AND refs != '' ORDER BY id`,
    { p: project, t: trace });
}
export function readAttachmentRoots(probe: AttachmentReader, project: string, trace: string) {
  return probe.ch<{ id: string; name: string; observation_type: string }>(
    `SELECT id, name, observation_type FROM spans FINAL
      WHERE project_id={p:UUID} AND trace_id={t:String} AND is_deleted=0
        AND (parent_span_id IS NULL OR parent_span_id = '') ORDER BY id`, { p: project, t: trace });
}
export function readAttachmentMessages(probe: AttachmentReader, project: string, trace: string, span: string) {
  // Raw carrier and actual input column are separate observations. trace_detail.py:_parse_content
  // projects this input column as observation_span.input (parsed JSON for this fixture).
  return probe.ch<{ id: string; input: string; input_attribute: string }>(
    `SELECT id, input, attrs_string['input.value'] AS input_attribute FROM spans FINAL
      WHERE project_id={p:UUID} AND trace_id={t:String} AND id={s:String} AND is_deleted=0 ORDER BY id`,
    { p: project, t: trace, s: span });
}
export function readAttachmentSessions(probe: AttachmentReader, project: string, trace: string) {
  return probe.ch<{ session_id: string }>(
    `SELECT DISTINCT toString(trace_session_id) AS session_id FROM spans FINAL
      WHERE project_id={p:UUID} AND trace_id={t:String} AND is_deleted=0 ORDER BY session_id`,
    { p: project, t: trace });
}
