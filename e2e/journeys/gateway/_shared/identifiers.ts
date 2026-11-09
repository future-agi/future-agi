// Input validation is not tenant authorization. Readers always bind the minted
// org/project as well as the exact cohort. Empty cohorts must not pass vacuously.
export function requireId(value: string): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$/.test(value)) {
    throw new Error('Expected a bounded synthetic record ID');
  }
  return value;
}

export function requireUuid(value: string): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error('Expected a minted UUID');
  }
  return value;
}

export function requireCohort(values: readonly string[]): string[] {
  if (!values.length || values.length > 256 || new Set(values).size !== values.length) {
    throw new Error('Expected 1..256 distinct fixture IDs; split larger cohorts explicitly');
  }
  return values.map(requireId);
}
