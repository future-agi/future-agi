import type { StateProbe } from '../../../lib/state-probe';
import { requireUuid } from './identifiers';

type Probe = Pick<StateProbe, 'pg'>;
export interface ExportConnectionRecord {
  id: string; workspace_id: string; platform: string; status: string;
  sync_interval_seconds: number; deleted: boolean; created_at: string; updated_at: string;
}

/** Source: futureagi/integrations/models/integration_connection.py.
 * Include deleted rows for lifecycle/no-write comparisons. Do not read stored
 * encrypted credentials, certificates, sync_cursor, host URL or status_message.
 */
export function readExportConnections(probe: Probe, organizationId: string): Promise<ExportConnectionRecord[]> {
  return probe.pg<ExportConnectionRecord>(`SELECT id::text, workspace_id::text, platform,
    status, sync_interval_seconds, deleted, created_at::text, updated_at::text
    FROM integrations_connection WHERE organization_id = $1 ORDER BY created_at, id`,
  [requireUuid(organizationId)]);
}

/** Compare the exact fixture URL in PG; don't return a potentially credentialed URL. */
export function readExportEndpointEquality(
  probe: Probe, organizationId: string, connectionId: string, expectedUrl: string,
): Promise<{ id: string; host_matches: boolean }[]> {
  return probe.pg(`SELECT id::text, host_url = $3 AS host_matches FROM integrations_connection
    WHERE organization_id = $1 AND id = $2`,
  [requireUuid(organizationId), requireUuid(connectionId), expectedUrl]);
}

export interface ExportSyncRecord {
  id: string; connection_id: string; status: string; started_at: string;
  completed_at: string | null; traces_fetched: number; traces_created: number;
  traces_updated: number; spans_synced: number; scores_synced: number;
  error_kind: string | null; sync_from: string | null; sync_to: string | null; deleted: boolean;
}

/** Source: models/sync_log.py:SyncLog (no organization column; join connection).
 * error_details.kind: integrations/services/export_failures.py:ERROR_DETAIL_FIELDS.
 * No error_message or unbounded error_details payload. Return every run so a
 * duplicate dispatch cannot hide behind selection of one expected sync-log ID.
 */
export function readExportSyncs(
  probe: Probe, organizationId: string, connectionId: string,
): Promise<ExportSyncRecord[]> {
  return probe.pg<ExportSyncRecord>(`SELECT s.id::text, s.connection_id::text, s.status,
    s.started_at::text, s.completed_at::text, s.traces_fetched, s.traces_created,
    s.traces_updated, s.spans_synced, s.scores_synced, s.error_details->>'kind' AS error_kind,
    s.sync_from::text, s.sync_to::text, s.deleted
    FROM integrations_sync_log s JOIN integrations_connection c ON c.id = s.connection_id
    WHERE c.organization_id = $1 AND c.id = $2 ORDER BY s.started_at, s.id`,
  [requireUuid(organizationId), requireUuid(connectionId)]);
}
