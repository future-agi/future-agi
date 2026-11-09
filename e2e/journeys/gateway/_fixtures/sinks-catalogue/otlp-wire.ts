// Source: da4c81cc623f909ce38f53e01b557ee8374d5adb
// agentcc-gateway/internal/otel/otlp.go:encode/encodeSpan/keyValue;
// agentcc-gateway/go.mod locks go.opentelemetry.io/proto/otlp v1.5.0.
// Field numbers verified from that dependency's trace/v1/trace.pb.go,
// common/v1/common.pb.go and resource/v1/resource.pb.go. This is a STRICT
// decoder for that gateway emitter, not a general-purpose OTLP implementation.
// JSON, unknown fields, duplicate singular fields/attribute keys, truncated
// values, missing timestamps and unsupported wire variants fail closed.
// Decoded values are ephemeral INSIDE the sink; never expose them to a reporter.
import { Buffer } from 'node:buffer';

const MAX_BODY = 8 * 1024 * 1024; // emitter otlpMaxBodyBytes
const utf8 = new TextDecoder('utf-8', { fatal: true });
type Field = { id: number; wire: number; value: Buffer | bigint };
const invalid = (): never => { throw new Error('OTLP_WIRE_INVALID'); };
function fields(data: Buffer): Field[] {
  let cursor = 0;
  const out: Field[] = [];
  const readVarint = (): bigint => {
    let value = 0n;
    for (let i = 0; i < 10; i++) {
      if (cursor >= data.length) return invalid();
      const byte = data[cursor++];
      if (i === 9 && byte > 1) return invalid();
      value |= BigInt(byte & 127) << BigInt(i * 7);
      if (!(byte & 128)) return value;
    }
    return invalid();
  };
  while (cursor < data.length) {
    if (out.length >= 65_536) return invalid();
    const tag = readVarint();
    const id = Number(tag >> 3n); const wire = Number(tag & 7n);
    if (id < 1 || id > 536_870_911) return invalid();
    if (wire === 0) { out.push({ id, wire, value: readVarint() }); continue; }
    const size = wire === 1 ? 8 : wire === 5 ? 4 : wire === 2 ? Number(readVarint()) : -1;
    if (!Number.isSafeInteger(size) || size < 0 || size > data.length - cursor) return invalid();
    out.push({ id, wire, value: data.subarray(cursor, cursor + size) }); cursor += size;
  }
  return out;
}
function shape(data: Buffer, types: Record<number, number>, repeated: number[] = []): Field[] {
  const rows = fields(data); const seen = new Set<number>();
  for (const row of rows) {
    if (types[row.id] !== row.wire || (seen.has(row.id) && !repeated.includes(row.id))) return invalid();
    seen.add(row.id);
  }
  return rows;
}
function buffer(row: Field | undefined): Buffer {
  if (!row || !Buffer.isBuffer(row.value)) return invalid();
  return row.value;
}
const text = (row: Field | undefined) => utf8.decode(buffer(row));
function attributes(rows: Field[], id: number): Map<string, unknown> {
  const attrs = new Map<string, unknown>();
  for (const row of rows.filter(r => r.id === id)) {
    const pair = shape(buffer(row), { 1: 2, 2: 2 });
    const key = text(pair.find(r => r.id === 1));
    if (!key || attrs.has(key)) return invalid();
    attrs.set(key, anyValue(buffer(pair.find(r => r.id === 2)), 0));
  }
  return attrs;
}
function anyValue(data: Buffer, depth: number): unknown {
  if (depth > 8) return invalid();
  const rows = shape(data, { 1: 2, 2: 0, 3: 0, 4: 1, 5: 2, 6: 2, 7: 2 });
  if (rows.length !== 1) return invalid();
  const r = rows[0];
  if (r.id === 1) return text(r);
  if (r.id === 2) { if (r.value !== 0n && r.value !== 1n) return invalid(); return r.value === 1n; }
  if (r.id === 3) return BigInt.asIntN(64, r.value as bigint);
  if (r.id === 4) { const n = buffer(r).readDoubleLE(); if (!Number.isFinite(n)) return invalid(); return n; }
  if (r.id === 7) return buffer(r);
  const items = shape(buffer(r), { 1: 2 }, [1]);
  if (r.id === 5) return items.map(item => anyValue(buffer(item), depth + 1));
  const map = new Map<string, unknown>();
  for (const item of items) {
    const pair = shape(buffer(item), { 1: 2, 2: 2 });
    const key = text(pair.find(p => p.id === 1));
    if (!key || map.has(key)) return invalid();
    map.set(key, anyValue(buffer(pair.find(p => p.id === 2)), depth + 1));
  }
  return map;
}
export interface DecodedSpan {
  attributes: Map<string, unknown>;
  resourceAttributes: Map<string, unknown>;
  // Includes names, status, scope and resource values for synthetic-sentinel scan.
  // Only the sink consumes this; it never returns/attaches/logs this structure.
  scannedValues: unknown[];
}
export function decodeGatewayOtlp(data: Buffer): DecodedSpan[] {
  try {
    if (!Buffer.isBuffer(data) || data.length === 0 || data.length > MAX_BODY) return invalid();
    const top = shape(data, { 1: 2 }, [1]);
    if (top.length === 0) return invalid();
    const spans: DecodedSpan[] = [];
    for (const resource of top) {
      const rs = shape(buffer(resource), { 1: 2, 2: 2 }, [2]);
      const rd = shape(buffer(rs.find(r => r.id === 1)), { 1: 2 }, [1]);
      const resourceAttrs = attributes(rd, 1);
      for (const scope of rs.filter(r => r.id === 2)) {
        const ss = shape(buffer(scope), { 1: 2, 2: 2 }, [2]);
        const scopeFields = shape(buffer(ss.find(s => s.id === 1)), { 1: 2 });
        const name = text(scopeFields.find(s => s.id === 1));
        if (name !== 'github.com/futureagi/agentcc-gateway') return invalid();
        for (const encoded of ss.filter(s => s.id === 2)) {
          if (spans.length >= 4096) return invalid();
          const s = shape(buffer(encoded), { 1: 2, 2: 2, 4: 2, 5: 2, 6: 0, 7: 1, 8: 1, 9: 2, 15: 2 }, [9]);
          for (const [id, size] of [[1, 16], [2, 8]]) {
            const value = buffer(s.find(f => f.id === id));
            if (value.length !== size || value.every(v => v === 0)) return invalid();
          }
          const parent = s.find(f => f.id === 4);
          if (parent && (buffer(parent).length !== 8 || buffer(parent).every(v => v === 0))) return invalid();
          const spanName = text(s.find(f => f.id === 5));
          if (!spanName || s.find(f => f.id === 6)?.value !== 3n) return invalid();
          const start = buffer(s.find(f => f.id === 7)).readBigUInt64LE();
          const end = buffer(s.find(f => f.id === 8)).readBigUInt64LE();
          if (start === 0n || end < start) return invalid();
          const attrs = attributes(s, 9);
          const values: unknown[] = [spanName, name, resourceAttrs, attrs];
          const status = s.find(f => f.id === 15);
          if (status) {
            const st = shape(buffer(status), { 2: 2, 3: 0 });
            const code = st.find(f => f.id === 3)?.value;
            if (code !== undefined && code !== 0n && code !== 1n && code !== 2n) return invalid();
            if (st.some(f => f.id === 2)) values.push(text(st.find(f => f.id === 2)));
          }
          spans.push({ attributes: attrs, resourceAttributes: resourceAttrs, scannedValues: values });
        }
      }
    }
    if (!spans.length) return invalid();
    return spans;
  } catch { return invalid(); } // Do not leak protobuf/string data in decoder errors.
}
