export function getJob(id: string): {
  population: Record<string, number>;
  checks: Array<{ id: string; kind: string; sources: string[] }>;
  steps: Array<{ id: string; phase: string; goal: string; human_actions: string[] }>;
  fault_ids: string[];
};
export function digest(value: unknown): string;
export function sha256(value: string | Uint8Array): string;
