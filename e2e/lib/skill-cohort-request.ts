// Which browser request is the Skills version-cohort projection (OBS-E2E-070). The page sends
// other POSTs with differently shaped bodies first or concurrently; the trace list, for one,
// sends `filters` as a JSON string. So identify the endpoint before reading `filters`, never
// assume their shape, and never throw: a waiter predicate that throws ends the wait.
export interface CohortTarget {
  projectId: string;
  versionId: string;
}

const COHORT_PATH = '/tracer/trace/skill-projection/';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function filtersTheVersion(entry: unknown, versionId: string): boolean {
  if (!isRecord(entry) || !isRecord(entry.filter_config)) return false;
  const config = entry.filter_config;
  return entry.column_id === 'skill.version_id'
    && config.col_type === 'SKILL'
    && config.filter_type === 'text'
    && config.filter_op === 'equals'
    && config.filter_value === versionId
    && (entry.group ?? 0) === 0;
}

export function isSkillCohortRequest(method: string, pathname: string, body: unknown, target: CohortTarget): boolean {
  if (method !== 'POST' || !pathname.endsWith(COHORT_PATH) || !isRecord(body)) return false;
  if (body.projection !== 'cohort' || body.project_id !== target.projectId) return false;
  if (typeof body.from !== 'string' || typeof body.to !== 'string') return false;
  const from = Date.parse(body.from);
  const to = Date.parse(body.to);
  if (!(Number.isFinite(from) && Number.isFinite(to) && from < to)) return false;
  return Array.isArray(body.filters) && body.filters.some((entry) => filtersTheVersion(entry, target.versionId));
}

// One non-secret line about the observed cohort response, for CI summaries: the HTTP status, the
// error envelope's `code` (or `none`), and the request's own elapsed time. No body, headers or ids.
// e2e/scripts/skills-coverage.py prints only lines that match this exact shape.
export const OBSERVATION_TYPE = 'skills-observation';

export function cohortObservationLine(status: number | null, code: unknown, elapsedMs: number | null): string {
  const safeStatus = typeof status === 'number' && Number.isInteger(status) && status >= 100 && status <= 599 ? String(status) : '000';
  const safeCode = typeof code === 'string' && /^[A-Za-z0-9_.-]{1,64}$/.test(code) ? code.toLowerCase()
    : code === undefined || code === null ? 'none' : 'other';
  const ms = typeof elapsedMs === 'number' && Number.isFinite(elapsedMs) && elapsedMs >= 0 ? Math.min(Math.round(elapsedMs), 9_999_999) : 0;
  return `cohort status=${safeStatus} code=${safeCode} elapsed_ms=${ms}`;
}
