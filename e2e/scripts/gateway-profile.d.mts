// Types for gateway-profile.mjs (imported by harness/walk-harness.spec.ts).
export interface ProfileRequirement { file: string; contains?: string }
export interface ProfileEntry {
  file: string; stacks: string[]; requires: ProfileRequirement[]; overlays: string[]; services: string[];
  unblockedBy: string;
}
export const TABLE_FILE: string;
export function loadTable(file?: string): Record<string, ProfileEntry>;
export function readiness(table: Record<string, ProfileEntry>, profile: string, stack: string,
  read: (file: string) => string | null): { entry: ProfileEntry; missing: string[] };
