import type { ApiClient } from '../../../../lib/api-client';
import { requireUuid } from '../../_shared/identifiers';

// da4c81cc62: integrations/urls.py; views/sync_log.py:list;
// serializers/{sync_log,contracts}.py; models/sync_log.py:SyncStatus.
// These are the exact endpoint,
// zero-based page metadata and public fields consumed by the Integrations UI.
// This API filters soft-deletes, so join with _shared/readExportSyncs when
// proving absence/coalescing; API history alone is not authoritative for them.
export interface SafeSync {
  id: string; connectionId: string; status: string; tracesFetched: number;
}
type Reader = Pick<ApiClient, 'baseURL' | 'get'>;
const invalid = (): never => { throw new Error('EXPORT_SYNC_API_SHAPE'); };
function object(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return invalid();
  return v as Record<string, unknown>;
}
function integer(v: unknown): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < 0) return invalid();
  return v;
}
export async function readAllSyncPages(api: Reader, connectionId: string): Promise<SafeSync[]> {
  requireUuid(connectionId);
  const url = new URL(api.baseURL);
  if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      url.username || url.password) throw new Error('EXPORT_LOOPBACK_REQUIRED');
  const rows: SafeSync[] = []; const seen = new Set<string>(); let total: number | undefined;
  // Fixed safety ceiling, not a truncating limit. If exceeded, fail the read.
  // Runtime must quiesce the connection before this non-snapshot paginated API.
  for (let page = 0; page < 100; page++) {
    let response: unknown;
    try {
      response = await api.get<unknown>('/integrations/sync-logs/', {
        connection_id: connectionId, page_number: page, page_size: 20,
      });
    } catch { throw new Error('EXPORT_SYNC_API_READ_FAILED'); }
    const body = object(response);
    if (body.status !== true) return invalid();
    const result = object(body.result); const meta = object(result.metadata);
    const count = integer(meta.total_count);
    if (total !== undefined && count !== total) throw new Error('EXPORT_SYNC_API_CHANGED');
    total = count;
    const pages = Math.ceil(count / 20);
    const next = page + 1 < pages ? page + 1 : null;
    if (meta.current_page !== page || meta.page_size !== 20 || meta.total_pages !== pages || meta.next_page !== next ||
        !Array.isArray(result.sync_logs) || result.sync_logs.length !== Math.min(20, count - page * 20)) return invalid();
    for (const value of result.sync_logs) {
      const r = object(value);
      if (typeof r.id !== 'string' || typeof r.connection !== 'string' || r.connection !== connectionId ||
          typeof r.status !== 'string' || !['success', 'partial', 'failed', 'rate_limited', 'no_new_data'].includes(r.status)) return invalid();
      const id = requireUuid(r.id);
      if (seen.has(id)) throw new Error('EXPORT_SYNC_API_DUPLICATE');
      seen.add(id);
      rows.push({ id, connectionId, status: r.status, tracesFetched: integer(r.traces_fetched) });
    }
    if (next === null) {
      if (rows.length !== total) return invalid();
      return rows;
    }
  }
  throw new Error('EXPORT_SYNC_API_BUDGET');
}
