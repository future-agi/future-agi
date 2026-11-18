import { test, expect } from '@playwright/test';
import {
  assertImmutableAttachmentFacts, assertReadyAttachmentOccurrences,
  readAttachmentOccurrences, readAttachmentRefs, readAttachmentRoots, readAttachmentMessages, readAttachmentSessions,
  type AttachmentReader, type AttachmentRow, type AttachmentSeedFacts,
} from '../lib/attachment-probe';

// H0 database-free contract tests only. The stub returns observations; it performs no
// business logic/SQL seeding. These do NOT prove applied migrations, SQL execution,
// actual SDK ingestion or any managed OBS-E2E-066 claim.
// 60s ASYNC_JOB + 15s SPAN_VISIBLE + 45s setup/assertions = 120s ceiling per case.
const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const scope = { organizationId: id(1), workspaceId: id(2), projectId: id(3) };
const seed: AttachmentSeedFacts = Object.freeze({
  occurrence: id(4), attachment: id(5), organization_id: id(1), workspace_id: id(2), project_id: id(3),
  capture_session: id(6), correlation_nonce: id(7), writer_kind: 'api_key',
  trace_id: id(8), span_id: '0123456789abcdef', direction: 'input', purpose: 'model_input',
  message_index: 1, part_index: 1, role: 'user', mime_type: 'image/png', verified_mime_type: 'image/png',
  verified_byte_length: '144', verified_sha256: 'a'.repeat(64),
});
const observed: AttachmentRow = Object.freeze({
  ...seed, occurrence_internal_id: id(9), attachment_internal_id: id(10), scope_id: id(11),
  capture_internal_id: id(12), writer_id: id(13), bound: true, deleted: false, state: 'ready',
  storage_binding_id: id(14), binding_fingerprint: 'b'.repeat(64), verified_blob_id: id(15),
  verified_blob_created_at: '2026-11-16 00:00:00+00', verified_storage_key: 'synthetic/original',
  verified_storage_version: 'version-one', verified_at: '2026-11-16 00:00:01+00',
  verified_sample_rate: null, verified_channels: null, verified_frame_count: null,
});

function transport(rows: unknown[]) {
  const calls: { lane: string; sql: string; params: unknown }[] = [];
  const probe: AttachmentReader = {
    pg: async <T>(sql: string, params?: unknown[]) => { calls.push({ lane: 'pg', sql, params }); return rows as T[]; },
    ch: async <T>(sql: string, params?: Record<string, string | number>) => {
      calls.push({ lane: 'ch', sql, params }); return rows as T[];
    },
  };
  return { probe, calls };
}

test('H0: parameterized PG reads use only the frozen Attachment verified fields and exact scope joins', async () => {
  test.setTimeout(120_000);
  const { probe, calls } = transport([observed]);
  const rows = await readAttachmentOccurrences(probe, scope, [seed.occurrence]);
  assertReadyAttachmentOccurrences(rows, [seed]);
  expect(calls).toHaveLength(1);
  expect(calls[0].params).toStrictEqual([scope.organizationId, scope.workspaceId, scope.projectId, [seed.occurrence]]);
  const sql = calls[0].sql;
  expect(sql).toContain('a.scope_id = o.scope_id');
  expect(sql).toContain('c.scope_id = o.scope_id');
  expect(sql).toContain('s.organization_id = $1 AND s.workspace_id = $2 AND s.project_id = $3');
  expect(sql).toContain('o.client_id = ANY($4::uuid[])');
  for (const column of ['verified_byte_length', 'verified_sha256', 'verified_mime_type',
    'verified_storage_key', 'verified_storage_version', 'verified_blob_id', 'verified_blob_created_at', 'verified_at'])
    expect(sql).toContain(`a.${column}`);
  expect(sql).toContain('b.id = a.storage_binding_id');
  expect(sql).not.toContain('tracer_attachmentblob');
  expect(sql).not.toMatch(/\b(?:INSERT|UPDATE|DELETE|COALESCE)\b/i);
  expect(sql).not.toContain(seed.occurrence);
});

for (const [label, rows] of [
  ['zero rows', []], ['duplicate rows', [observed, observed]],
  ['wrong occurrence', [{ ...observed, occurrence: id(99) }]],
  ['foreign organization', [{ ...observed, organization_id: id(99) }]],
  ['foreign workspace', [{ ...observed, workspace_id: id(99) }]],
  ['foreign project', [{ ...observed, project_id: id(99) }]],
  ['wrong attachment', [{ ...observed, attachment: id(99) }]],
  ['wrong capture session', [{ ...observed, capture_session: id(99) }]],
  ['wrong capture nonce', [{ ...observed, correlation_nonce: id(99) }]],
  ['wrong writer kind', [{ ...observed, writer_kind: 'user' }]],
  ['wrong trace parent', [{ ...observed, trace_id: id(99) }]],
  ['wrong span parent', [{ ...observed, span_id: 'fedcba9876543210' }]],
  ['wrong message placement', [{ ...observed, message_index: 2 }]],
  ['wrong part placement', [{ ...observed, part_index: 2 }]],
  ['wrong role placement', [{ ...observed, role: 'assistant' }]],
  ['wrong verified MIME', [{ ...observed, verified_mime_type: 'application/pdf' }]],
  ['unbound occurrence', [{ ...observed, bound: false }]],
  ['deleted occurrence', [{ ...observed, deleted: true }]],
  ['not ready', [{ ...observed, state: 'validating' }]],
] as const) {
  test(`H0: ${label} cannot satisfy the unchanged independent seed ledger`, async () => {
    test.setTimeout(120_000);
    const { probe } = transport([...rows]);
    const actual = await readAttachmentOccurrences(probe, scope, [seed.occurrence]);
    expect(() => assertReadyAttachmentOccurrences(actual, [seed])).toThrow();
  });
}

for (const [label, change] of [
  ['digest', { verified_sha256: 'c'.repeat(64) }],
  ['length', { verified_byte_length: '145' }],
  ['parent', { span_id: 'fedcba9876543210' }],
] as const) {
  test(`H0: expected-only ${label} mutation fails without changing observed/upload facts`, () => {
    test.setTimeout(120_000);
    assertReadyAttachmentOccurrences([observed], [seed]);
    expect(() => assertReadyAttachmentOccurrences([observed], [{ ...seed, ...change }])).toThrow(/seed facts/);
    assertReadyAttachmentOccurrences([observed], [seed]);
  });
}

for (const field of ['storage_binding_id', 'binding_fingerprint', 'verified_blob_id', 'verified_blob_created_at',
  'verified_storage_key', 'verified_storage_version', 'verified_sha256', 'verified_byte_length',
  'verified_mime_type', 'verified_at', 'writer_id'] as const) {
  for (const missing of [null, '']) {
    test(`H0: ready row requires ${field} (${missing === null ? 'null' : 'empty'})`, () => {
      test.setTimeout(120_000);
      // Deliberately violate the static result type: the SQL driver can return null in non-ready rows.
      const row = { ...observed, [field]: missing } as unknown as AttachmentRow;
      expect(() => assertReadyAttachmentOccurrences([row], [seed])).toThrow();
    });
  }
}

test('H0: zero verified length cannot be blessed even by an equally wrong expected ledger', () => {
  test.setTimeout(120_000);
  expect(() => assertReadyAttachmentOccurrences([{ ...observed, verified_byte_length: '0' }],
    [{ ...seed, verified_byte_length: '0' }])).toThrow(/positive/);
});

test('H0: empty and duplicate expected ledgers fail before any query', async () => {
  test.setTimeout(120_000);
  const { probe, calls } = transport([]);
  expect(() => readAttachmentOccurrences(probe, scope, [])).toThrow(/empty/);
  expect(() => readAttachmentOccurrences(probe, scope, [seed.occurrence, seed.occurrence])).toThrow(/duplicate/);
  expect(() => assertReadyAttachmentOccurrences([], [])).toThrow(/empty/);
  expect(() => assertReadyAttachmentOccurrences([observed, observed], [seed, seed])).toThrow(/duplicate/);
  expect(calls).toStrictEqual([]);
});

test('H0: full immutable snapshot survives tombstone/state changes; a missing row never passes', () => {
  test.setTimeout(120_000);
  assertReadyAttachmentOccurrences([observed], [seed]);
  assertImmutableAttachmentFacts([observed], [{ ...observed, deleted: true, state: 'deleted' }]);
  expect(() => assertImmutableAttachmentFacts([observed], [])).toThrow(/immutable/);
  expect(() => assertImmutableAttachmentFacts([], [])).toThrow(/empty/);
});

for (const [field, changed] of [
  ['verified_sha256', 'c'.repeat(64)], ['verified_byte_length', '145'], ['verified_mime_type', 'image/jpeg'],
  ['verified_storage_key', 'synthetic/other'], ['verified_storage_version', 'version-two'],
  ['storage_binding_id', id(99)], ['binding_fingerprint', 'c'.repeat(64)], ['verified_blob_id', id(99)],
  ['verified_blob_created_at', '2026-11-16 00:00:02+00'], ['verified_at', '2026-11-16 00:00:02+00'],
  ['verified_sample_rate', 48000], ['verified_channels', 2], ['verified_frame_count', '9600'],
  ['capture_session', id(99)], ['writer_id', id(99)], ['trace_id', id(99)], ['span_id', 'fedcba9876543210'],
  ['message_index', 2], ['part_index', 2], ['role', 'assistant'],
] as const) {
  test(`H0: deletion cannot change or erase ${field}`, () => {
    test.setTimeout(120_000);
    expect(() => assertImmutableAttachmentFacts([observed],
      [{ ...observed, deleted: true, state: 'deleted', [field]: changed }])).toThrow(/immutable/);
    expect(() => assertImmutableAttachmentFacts([{ ...observed, [field]: changed }],
      [{ ...observed, deleted: true, [field]: null } as unknown as AttachmentRow])).toThrow();
  });
}

test('H0: CH reads bind exact project/trace/parent and always use FINAL', async () => {
  test.setTimeout(120_000);
  const { probe, calls } = transport([]);
  expect(await readAttachmentRefs(probe, scope.projectId, seed.trace_id)).toStrictEqual([]);
  expect(await readAttachmentRoots(probe, scope.projectId, seed.trace_id)).toStrictEqual([]);
  expect(await readAttachmentMessages(probe, scope.projectId, seed.trace_id, seed.span_id)).toStrictEqual([]);
  expect(await readAttachmentSessions(probe, scope.projectId, seed.trace_id)).toStrictEqual([]);
  expect(calls).toHaveLength(4);
  calls.forEach((call, index) => {
    expect(call.lane).toBe('ch');
    expect(call.sql).toContain('FROM spans FINAL');
    expect(call.sql).toContain('project_id={p:UUID} AND trace_id={t:String}');
    expect(call.sql).toContain('is_deleted=0');
    expect(call.params).toStrictEqual({ p: scope.projectId, t: seed.trace_id, ...(index === 2 ? { s: seed.span_id } : {}) });
    expect(call.sql).not.toContain(seed.trace_id);
  });
  expect(calls[2].sql).toContain('id={s:String}');
  expect(calls[2].sql).toContain("input, attrs_string['input.value'] AS input_attribute");
});
