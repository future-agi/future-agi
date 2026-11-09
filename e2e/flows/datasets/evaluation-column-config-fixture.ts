import pg from 'pg';
import { E2E } from '../../lib/env';

// Flow-local fixture for DATA-E2E-099. No product path writes a column that
// carries an experiment evaluation's source id outside that experiment's own
// dataset: dataset clone and duplicate copy only plain columns, without a
// source id. The flow creates every dataset, row and cell through the public
// API and uses this writer for that one relabel only. It never inserts or
// deletes rows, and the statement is bound to one plain column of one dataset
// of one organization.
export interface EvaluationRelabel {
  organizationId: string; datasetId: string; columnId: string; name: string; sourceId: string;
}

export async function relabelAsExperimentEvaluation(r: EvaluationRelabel): Promise<void> {
  if (new URL(E2E.pgUrl).hostname !== 'localhost') throw new Error('fixture writer requires the local stack');
  const client = new pg.Client({ connectionString: E2E.pgUrl });
  await client.connect();
  try {
    // model_hub/models/develop_dataset.py Column: name, source, source_id.
    const columns = await client.query(
      `UPDATE model_hub_column c SET name=$4, source='experiment_evaluation', source_id=$5
       FROM model_hub_dataset d
       WHERE c.id=$1 AND c.dataset_id=$2 AND d.id=c.dataset_id AND d.organization_id=$3
         AND d.deleted=false AND c.deleted=false AND c.source='OTHERS' AND c.source_id IS NULL
       RETURNING c.id`,
      [r.columnId, r.datasetId, r.organizationId, r.name, r.sourceId]);
    if (columns.rowCount !== 1) throw new Error('fixture column relabel did not match exactly one column');
  } finally {
    await client.end();
  }
}
