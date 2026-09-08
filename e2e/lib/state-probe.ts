import pg from 'pg';
import { ApiClient } from './api-client';

export const POLL = {
  // Collector inserts synchronously per export batch; SDK flush dominates.
  SPAN_VISIBLE: { timeout: 15_000, intervals: [500, 1000, 2000] },
  // Temporal eval task reaching completed: schedule ticks every 10s + worker latency.
  EVAL_RESULT: { timeout: 90_000, intervals: [2000, 5000] },
  // PG→ClickHouse via the PeerDB CDC mirror: ~20.7s typical, but observed
  // past 90s under parallel load. Used by any read of a CDC-fed table.
  CDC_VISIBLE: { timeout: 180_000, intervals: [2000, 5000] },
  ASYNC_JOB: { timeout: 60_000, intervals: [2000, 5000] },
};

export class StateProbe {
  private pool: pg.Pool;

  constructor(private cfg: { api: ApiClient; chUrl: string; chDatabase: string; pgUrl: string }) {
    this.pool = new pg.Pool({ connectionString: cfg.pgUrl, max: 2 });
  }

  /** Read-only ClickHouse query over HTTP with server-side param binding:
   *  probe.ch('SELECT … WHERE project_id = {p:UUID}', { p: id }) */
  async ch<T>(query: string, params: Record<string, string | number> = {}): Promise<T[]> {
    const url = new URL(this.cfg.chUrl);
    url.searchParams.set('database', this.cfg.chDatabase);
    url.searchParams.set('default_format', 'JSONEachRow');
    for (const [k, v] of Object.entries(params)) url.searchParams.set(`param_${k}`, String(v));
    const res = await fetch(url, { method: 'POST', body: query });
    if (!res.ok) throw new Error(`CH ${res.status}: ${await res.text()}`);
    const text = (await res.text()).trim();
    return text ? text.split('\n').map(line => JSON.parse(line) as T) : [];
  }

  async pg<T extends pg.QueryResultRow>(text: string, values: unknown[] = []): Promise<T[]> {
    return (await this.pool.query<T>(text, values)).rows;
  }

  /** The same list endpoint the UI calls — the preferred assertion lane. */
  async apiList<T>(path: string, params?: Record<string, string | number>): Promise<T[]> {
    const body = await this.cfg.api.get<{ result: { table: T[] } }>(path, params);
    return body.result.table;
  }

  async dispose() { await this.pool.end(); }
}
