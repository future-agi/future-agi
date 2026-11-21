import pg from 'pg';
import { E2E } from './env';

// The shortest values the product accepts (tracer/models/derived_metric.py:
// the metric_refresh_settle_minutes constraint and CADENCE_SECONDS_MIN). The
// defaults are 30 minutes and 900 seconds, and no API sets either field.
export const SHORT_SETTLE_MINUTES = 1;
export const SHORT_CADENCE_SECONDS = 60;

/** Sets ONE project's metric refresh to the shortest settle and cadence the
 * product accepts, through its own connection; the StateProbe stays
 * read-only. A flow that waits for a settled metric value then waits a minute
 * and not the default half hour, on the same refresh path. The row exists
 * once the project has a saved metric. Fails unless exactly one row changed
 * and the values the UPDATE returns match, so a wrong id or a wrong
 * E2E_PG_URL (attach mode) is loud. */
export async function shortenMetricSettle(projectId: string): Promise<void> {
  let where: string; // host:port only, never the credentials
  try {
    where = new URL(E2E.pgUrl).host;
  } catch {
    throw new Error('metric settle: E2E_PG_URL is missing or not a URL (is E2E_PG_URL the stack\'s database?)');
  }
  const client = new pg.Client({ connectionString: E2E.pgUrl });
  try {
    await client.connect();
    const updated = await client.query<{ settle_minutes: number; cadence_seconds: number }>(
      'UPDATE tracer_metricprojectrefresh SET settle_minutes = $2, cadence_seconds = $3 WHERE project_id = $1 '
      + 'RETURNING settle_minutes, cadence_seconds',
      [projectId, SHORT_SETTLE_MINUTES, SHORT_CADENCE_SECONDS]);
    if (updated.rowCount !== 1) {
      throw new Error(`metric settle: project ${projectId} changed ${updated.rowCount} refresh rows `
        + `in Postgres at ${where}, expected exactly 1 (does the project have a saved metric yet?)`);
    }
    const stored = updated.rows[0];
    if (stored.settle_minutes !== SHORT_SETTLE_MINUTES || stored.cadence_seconds !== SHORT_CADENCE_SECONDS) {
      throw new Error(`metric settle: project ${projectId} returned settle ${stored.settle_minutes} min, `
        + `cadence ${stored.cadence_seconds} s; expected ${SHORT_SETTLE_MINUTES} and ${SHORT_CADENCE_SECONDS}`);
    }
  } finally {
    await client.end();
  }
}
