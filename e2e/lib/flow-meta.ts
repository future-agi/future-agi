export interface FlowMeta {
  id: string;            // <AREA>-E2E-<nnn>, e.g. OBS-E2E-001
  area: string;          // catalog grouping = flows/<area>/
  userGoal: string;
  steps: string[];       // the user actions, in order
  backendChecks: string[];
}

export function flowAnnotation(meta: FlowMeta): { type: 'flow'; description: string } {
  return { type: 'flow', description: JSON.stringify(meta) };
}
