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
