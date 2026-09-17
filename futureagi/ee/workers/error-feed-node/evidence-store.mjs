import {open} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
export function validateClaim(claim) {
  for (const key of ['organization_id', 'project_id', 'trace_id', 'job_id', 'attempt_id']) {
    if (!uuid.test(claim?.[key] ?? '')) throw new Error('Invalid claim identity');
  }
  if (claim.workspace_id !== null && !uuid.test(claim.workspace_id ?? '')) throw new Error('Invalid workspace identity');
  if (claim.contract_version !== 'omega-investigation/v1' || !claim.feature_enabled
      || !Number.isSafeInteger(claim.generation) || claim.generation < 1
      || !Number.isFinite(Date.parse(claim.read_cutoff))) throw new Error('Unsupported or disabled claim');
  for (const key of ['deadline_seconds', 'max_model_calls', 'max_input_tokens_total', 'max_output_tokens_total', 'max_evidence_bytes', 'max_tool_result_bytes']) {
    if (!Number.isSafeInteger(claim.limits?.[key]) || claim.limits[key] < 1) throw new Error('Invalid claim limit');
  }
}

// The project ownership proof comes from Django's claimed job, never the model.
// created_at/updated_at cutoff excludes later rows but is not an MVCC snapshot:
// a replacement merge may remove an earlier version between attempts.
export async function downloadEvidence(claim, path, {baseUrl, database, username, password, signal, fetchImpl = fetch}) {
  validateClaim(claim);
  if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(database)) throw new Error('Invalid ClickHouse database');
  const endpoint = new URL(baseUrl);
  if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) throw new Error('Invalid ClickHouse endpoint');
  endpoint.searchParams.set('database', database);
  endpoint.searchParams.set('param_project', claim.project_id);
  endpoint.searchParams.set('param_trace', claim.trace_id);
  endpoint.searchParams.set('param_org', claim.organization_id);
  endpoint.searchParams.set('param_cutoff', new Date(claim.read_cutoff).toISOString().replace('T', ' ').replace('Z', ''));
  const maxBytes = Math.min(claim.limits.max_evidence_bytes, 256 * 1024 * 1024);
  const maxRows = 50000;
  const query = `SELECT * FROM spans FINAL
PREWHERE project_id = {project:UUID} AND trace_id = {trace:String}
WHERE (org_id = {org:UUID} OR isNull(org_id)) AND is_deleted = 0
AND created_at <= {cutoff:DateTime64(6)} AND updated_at <= {cutoff:DateTime64(6)}
ORDER BY start_time, id LIMIT ${maxRows + 1}
SETTINGS max_execution_time=30, max_result_bytes=${maxBytes}, result_overflow_mode='throw', max_threads=1, max_memory_usage=134217728
FORMAT JSONEachRow`;
  const response = await fetchImpl(endpoint, {method: 'POST', body: query, signal, redirect: 'error',
    headers: {'Content-Type': 'text/plain', 'X-ClickHouse-User': username, 'X-ClickHouse-Key': password}});
  if (!response.ok || !response.body) { await response.body?.cancel(); throw new Error('ClickHouse evidence read failed'); }
  return storeEvidence(response.body, path, claim, {signal, maxBytes, maxRows});
}

export async function storeEvidence(chunks, path, claim, {signal, maxBytes = claim.limits.max_evidence_bytes, maxRows = 50000} = {}) {
  const file = await open(path, 'wx', 0o600);
  const digest = createHash('sha256');
  const index = new Map();
  let bytes = 0, pending = Buffer.alloc(0), offset = 0, unresolvedExternalPayloads = 0;
  function indexLine(line) {
    const row = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(line));
    if (row.project_id !== claim.project_id || row.trace_id !== claim.trace_id
        || (row.org_id && row.org_id !== claim.organization_id)) throw new Error('Evidence scope mismatch');
    if (typeof row.id !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(row.id) || index.has(row.id)) throw new Error('Invalid or duplicate span identity');
    if (index.size >= maxRows) throw new Error('Evidence row limit exceeded');
    const externalPayloads = ['input_gcs_url', 'output_gcs_url'].filter(key => {
      const value = row[key];
      return value !== null && value !== undefined
        && (typeof value !== 'string' || value.trim().length > 0);
    });
    unresolvedExternalPayloads += externalPayloads.length;
    index.set(row.id, {span_id: row.id, parent_span_id: row.parent_span_id ?? '',
      name: String(row.name ?? '').slice(0, 256), start_time: row.start_time, end_time: row.end_time,
      ...(externalPayloads.length ? {unresolved_external_payloads: externalPayloads} : {}),
      offset, bytes: line.length});
    offset += line.length + 1;
  }
  try {
    for await (const chunk of chunks) {
      signal?.throwIfAborted();
      const data = Buffer.from(chunk);
      bytes += data.length;
      if (bytes > maxBytes) throw new Error('Evidence byte limit exceeded');
      digest.update(data);
      // Write all bytes even if the OS performs a partial write.
      let written = 0;
      while (written < data.length) written += (await file.write(data, written, data.length - written)).bytesWritten;
      pending = Buffer.concat([pending, data]);
      let newline;
      while ((newline = pending.indexOf(10)) >= 0) {
        indexLine(pending.subarray(0, newline));
        pending = pending.subarray(newline + 1);
      }
    }
    if (pending.length) indexLine(pending);
    if (!index.size) throw new Error('No visible trace evidence');
    await file.sync();
    return {path, bytes, digest: 'sha256:' + digest.digest('hex'), index,
      coverage: {scope: 'trace_at_read_cutoff', observed_span_count: index.size,
        read_complete: unresolvedExternalPayloads === 0, future_arrivals_known: false}};
  } finally { await file.close(); }
}

// File offsets are host-owned. The model cannot select a path or execute a query.
export function createEvidenceReader(store, {maxResultBytes, maxTotalBytes, signal}) {
  const receipts = new Map();
  const readRanges = new Map();
  let readBytes = 0;
  function fullyRead(span) {
    const ranges = (readRanges.get(span.span_id) ?? []).sort((a, b) => a[0] - b[0]);
    let covered = 0;
    for (const [start, end] of ranges) {
      if (start > covered) break;
      covered = Math.max(covered, end);
    }
    return covered >= span.bytes;
  }
  return {
    allSpansRead() {
      for (const span of store.index.values()) if (!fullyRead(span)) return false;
      return true;
    },
    unreadSpanIds(limit = 20) {
      const ids = [];
      for (const span of store.index.values()) {
        if (!fullyRead(span)) ids.push(span.span_id);
        if (ids.length >= limit) break;
      }
      return ids;
    },
    inventory(cursor = 0) {
      if (!Number.isSafeInteger(cursor) || cursor < 0 || cursor > store.index.size) throw new Error('Invalid inventory cursor');
      const rows = [];
      for (const {offset, ...row} of [...store.index.values()].slice(cursor, cursor + 20)) {
        if (Buffer.byteLength(JSON.stringify([...rows, row])) + 128 > maxResultBytes) {
          if (!rows.length) throw new Error('Inventory row exceeds tool result budget');
          break;
        }
        rows.push(row);
      }
      return {spans: rows, next_cursor: cursor + rows.length, more: cursor + rows.length < store.index.size};
    },
    async read(spanId, offset, length) {
      signal?.throwIfAborted();
      const span = store.index.get(spanId);
      if (!span || !Number.isSafeInteger(offset) || offset < 0 || offset >= span.bytes
          || !Number.isSafeInteger(length) || length < 1 || length > maxResultBytes) throw new Error('Invalid span range');
      const size = Math.min(length, span.bytes - offset, maxResultBytes - 256);
      if (size < 1) throw new Error('Tool result budget is too small');
      if (readBytes + size > maxTotalBytes) throw new Error('Evidence tool budget exhausted');
      readBytes += size;
      const file = await open(store.path, 'r');
      try {
        const buffer = Buffer.alloc(size);
        let read = 0;
        while (read < size) {
          const got = (await file.read(buffer, read, size - read, span.offset + offset + read)).bytesRead;
          if (!got) throw new Error('Evidence file changed');
          read += got;
        }
        let returned = size, result;
        for (;;) {
          let text;
          for (let trim = 0; trim <= 3; trim++) {
            try {
              text = new TextDecoder('utf-8', {fatal: true}).decode(buffer.subarray(0, returned - trim));
              returned -= trim;
              break;
            } catch { /* The last code point may cross the requested boundary. */ }
          }
          if (text === undefined || returned < 1) throw new Error('Read offset splits a UTF-8 character');
          result = {evidence_id: `${spanId}:${offset}:${returned}`, text,
            next_offset: offset + returned, more: offset + returned < span.bytes};
          const serializedBytes = Buffer.byteLength(JSON.stringify(result));
          if (serializedBytes <= maxResultBytes) break;
          returned = Math.floor(returned * maxResultBytes / serializedBytes) - 1;
          if (returned < 1) throw new Error('Tool result budget is too small');
        }
        readBytes -= size - returned;
        const evidenceId = result.evidence_id;
        const text = result.text;
        receipts.set(evidenceId, {evidence_id: evidenceId, span_id: spanId, parent_span_id: span.parent_span_id || null,
          excerpt: text.slice(0, 8000)});
        const ranges = readRanges.get(spanId) ?? [];
        ranges.push([offset, offset + returned]);
        readRanges.set(spanId, ranges);
        return result;
      } finally { await file.close(); }
    },
    receipts: () => [...receipts.values()],
  };
}
