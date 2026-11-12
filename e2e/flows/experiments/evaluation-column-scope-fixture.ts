import pg from 'pg';
import { E2E } from '../../lib/env';

// Flow-local fixture for EXP-E2E-007. No product path writes a column whose
// source_id ends in another evaluation's "-sourceid-<metric id>": dataset
// clone and duplicate copy only plain columns, without source_id. The flow
// creates every dataset, row and cell through the public API and uses this
// writer for that one relabel only. It never inserts or deletes rows, and
// each statement is bound to one column of one dataset of one organization.
export interface Relabel {
  organizationId: string; datasetId: string; columnId: string;
  name: string; source: string; sourceId: string; cellValue: string;
}

export async function relabelColumns(relabels: Relabel[]): Promise<void> {
  if (new URL(E2E.pgUrl).hostname !== 'localhost') throw new Error('fixture writer requires the local stack');
  const client = new pg.Client({ connectionString: E2E.pgUrl });
  await client.connect();
  try {
    await client.query('BEGIN');
    for (const r of relabels) {
      // model_hub/models/develop_dataset.py Column: name, source, source_id.
      const columns = await client.query(
        `UPDATE model_hub_column c SET name=$4, source=$5, source_id=$6
         FROM model_hub_dataset d
         WHERE c.id=$1 AND c.dataset_id=$2 AND d.id=c.dataset_id AND d.organization_id=$3
           AND d.deleted=false AND c.deleted=false AND c.source='OTHERS' AND c.source_id IS NULL
         RETURNING c.id`,
        [r.columnId, r.datasetId, r.organizationId, r.name, r.source, r.sourceId]);
      if (columns.rowCount !== 1) throw new Error('fixture column relabel did not match exactly one column');
      // ManuallyCreateDatasetView writes one empty cell per row and column.
      const cells = await client.query(
        `UPDATE model_hub_cell SET value=$3
         WHERE dataset_id=$1 AND column_id=$2 AND deleted=false RETURNING id`,
        [r.datasetId, r.columnId, r.cellValue]);
      if (cells.rowCount !== 1) throw new Error('fixture cell write did not match exactly one cell');
    }
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    await client.end();
  }
}
