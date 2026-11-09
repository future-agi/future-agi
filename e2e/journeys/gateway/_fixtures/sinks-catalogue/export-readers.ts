import type { ReceivedWebhook } from '../../../../lib/sinks';
import type { StoredObject } from '../../../../lib/export-sinks';
import { requireId } from '../../_shared/identifiers';

// All wire paths/fields at da4c81cc62: e2e/lib/{sinks,export-sinks}.ts;
// e2e/stack/webhook-sink/server.mjs:exportContract/vendorAnswer/LISTED;
// integrations/temporal/activities.py:_export_datadog;
// integrations/services/{mixpanel,posthog,cloud_storage}_service.py.
// Synthetic credentials used for channel partitioning stay in memory. No
// returned object contains headers, payloads, error diagnostics or credentials.
export interface ExportProjection {
  deliveredIds: string[]; attemptedIds: string[]; successfulBatches: number; failedBatches: number;
}
export interface WebhookBinding {
  vendor: 'datadog' | 'mixpanel' | 'posthog'; channel: string; credential: string;
}
const shapeError = (): never => { throw new Error('EXPORT_SHAPE'); };
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return shapeError();
  return value as Record<string, unknown>;
}
function list(value: unknown): unknown[] { if (!Array.isArray(value)) return shapeError(); return value; }
function recordId(value: unknown): string {
  if (typeof value !== 'string') return shapeError();
  try { return requireId(value); } catch { return shapeError(); }
}
function scope(value: string): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_.-]{1,80}$/.test(value)) return shapeError();
  return value;
}
function local(url: string): void {
  const u = new URL(url);
  if (!['http:', 'https:'].includes(u.protocol) || !['127.0.0.1', '[::1]', 'localhost'].includes(u.hostname) ||
      u.username || u.password || u.pathname !== '/' || u.search || u.hash) throw new Error('EXPORT_LOOPBACK_REQUIRED');
}
export function projectWebhookRecords(records: readonly ReceivedWebhook[], binding: WebhookBinding): ExportProjection {
  const { vendor, credential } = binding; const channel = scope(binding.channel);
  if (!['datadog', 'mixpanel', 'posthog'].includes(vendor) || typeof credential !== 'string' ||
      !/^e2e-[A-Za-z0-9_.-]{1,100}$/.test(credential)) return shapeError();
  const out: ExportProjection = { deliveredIds: [], attemptedIds: [], successfulBatches: 0, failedBatches: 0 };
  const prefix = `/r/${channel}/as/${vendor}`;
  for (const raw of records) {
    const r = object(raw);
    const headers = object(r.headers);
    // PostHog alone has a dedicated per-test channel: a delivery under another
    // credential is contamination, not another worker's traffic to filter out.
    if (vendor === 'posthog' && r.path === `${prefix}/batch/` && object(r.body).api_key !== credential) return shapeError();
    // Shared Datadog/Mixpanel channels partition by the independently minted
    // credential, NEVER by the expected request IDs (which would hide extras).
    const mine = vendor === 'datadog' ? headers['dd-api-key'] === credential
      : vendor === 'posthog' ? object(r.body).api_key === credential
      : list(r.body).some(e => object(object(e).properties).token === credential);
    if (!mine) continue;
    if (typeof r.path !== 'string' || !r.path.startsWith(`${prefix}/`) ||
        typeof r.at !== 'string' || !Number.isFinite(Date.parse(r.at)) ||
        !Number.isInteger(r.status) || (r.status as number) < 0 || (r.status as number) > 599) return shapeError();
    const suffix = r.path.slice(prefix.length);
    // Exact non-record operations in source; these are not export deliveries.
    // posthog_service.py:validate_credentials at da4c81cc62 POSTs /decide/?v=3;
    // webhook-sink/server.mjs:handleRequest stores req.url INCLUDING the query.
    // Do not strip queries or accept arbitrary /decide traffic as metadata.
    const metadata = vendor === 'datadog' ? ['/api/v1/validate', '/api/v2/series']
      : vendor === 'posthog' ? ['/decide/?v=3'] : [];
    if (metadata.includes(suffix)) {
      if (vendor === 'posthog' && (r.method !== 'POST' ||
          object(r.body).distinct_id !== 'agentcc_connection_test')) return shapeError();
      continue;
    }
    const allowed = vendor === 'datadog' ? ['/api/v2/logs'] : vendor === 'posthog' ? ['/batch/'] : ['/track', '/import?strict=1'];
    if (r.method !== 'POST' || !allowed.includes(suffix)) return shapeError();
    const entries = vendor === 'posthog' ? list(object(r.body).batch) : list(r.body);
    if (entries.length === 0) return shapeError();
    const ids: string[] = [];
    for (const e of entries) {
      const entry = object(e);
      if (vendor === 'datadog') { ids.push(recordId(object(entry.attributes).request_id)); continue; }
      const properties = object(entry.properties);
      if (vendor === 'mixpanel' && properties.token !== credential) return shapeError();
      if (entry.event === '$agentcc_connection_test') {
        if (entries.length !== 1 || properties.request_id !== undefined) return shapeError();
        continue;
      }
      if (entry.event !== 'agentcc_request') return shapeError();
      ids.push(recordId(properties.request_id));
    }
    if (!ids.length) continue; // exactly the pinned connection-test event
    out.attemptedIds.push(...ids);
    const accepted = r.status === (vendor === 'datadog' ? 202 : 200);
    // /track's HTTP 200 may mean body '0'; ReceivedWebhook does not retain the
    // response body. Never count that ambiguous receipt as successful delivery.
    if (vendor === 'mixpanel' && suffix === '/track' && accepted) throw new Error('EXPORT_TRACK_ACK_OWED');
    if (accepted) { out.deliveredIds.push(...ids); out.successfulBatches++; }
    else out.failedBatches++;
  }
  return out;
}

/** Existing read API returns the WHOLE channel, with no pagination (LISTED
 * handler). Do not add imaginary limit/cursor query parameters. Guarded runtime
 * must bind the supplied loopback base to its inspected same-job webhook sink.
 */
export async function readWebhookExports(readUrl: string, binding: WebhookBinding): Promise<ExportProjection> {
  local(readUrl);
  // Same existing /__received API as WebhookSink.received; redirects must not
  // turn an approved loopback read into a nonlocal contact. Never print response
  // text or a transport exception (which might carry credential-bearing data).
  let records: ReceivedWebhook[];
  try {
    const response = await fetch(new URL(`/__received/${scope(binding.channel)}`, readUrl), {
      redirect: 'error', signal: AbortSignal.timeout(10_000),
    });
    if (response.status !== 200 || !response.body) throw new Error('read refused');
    const chunks: Uint8Array[] = []; let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > 16 * 1024 * 1024) throw new Error('read budget');
      chunks.push(chunk);
    }
    const body = object(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    if (body.channel !== binding.channel || !Array.isArray(body.requests)) throw new Error('read shape');
    records = body.requests as ReceivedWebhook[];
  } catch { throw new Error('EXPORT_SINK_READ_FAILED'); }
  return projectWebhookRecords(records, binding);
}

export function projectStorageRecords(objects: readonly StoredObject[], prefix: string): ExportProjection {
  scope(prefix);
  const out: ExportProjection = { deliveredIds: [], attemptedIds: [], successfulBatches: 0, failedBatches: 0 };
  const keys = new Set<string>();
  for (const row of objects) {
    if (typeof row.key !== 'string' || !row.key.startsWith(`${prefix}/`) ||
        row.key.split('/').some(p => p === '..' || p === '.' || !p) || keys.has(row.key)) {
      throw new Error('EXPORT_STORAGE_SCOPE');
    }
    keys.add(row.key);
    if (!Number.isSafeInteger(row.size) || row.size < 1 || row.contentType !== 'application/x-ndjson' ||
        row.contentEncoding !== 'gzip' || !Array.isArray(row.records) || !row.records.length) return shapeError();
    const ids = row.records.map(r => recordId(object(r).request_id));
    out.deliveredIds.push(...ids); out.attemptedIds.push(...ids); out.successfulBatches++;
  }
  return out;
}

/** StorageSink.objects enumerates ALL objects recursively and decodes JSONL.
 * Its existing implementation shells through bin/e2e, which is NOT allowed in a
 * consumer. The runner must supply its reviewed container-ID-bound equivalent
 * through E6. No StorageSink instance, subprocess or alternate dispatcher here.
 * Type/record shape is exactly the existing StorageSink.objects API.
 */
export interface BoundStorageReader { objects(prefix: string): Promise<StoredObject[]> }
export async function readStorageExports(reader: BoundStorageReader, prefix: string): Promise<ExportProjection> {
  scope(prefix);
  return projectStorageRecords(await reader.objects(prefix), prefix);
}
