export interface AuditHit {
  kind: string;
  /** A path LABEL: the path relative to the root, or "[path N: withheld]" when the path itself has a finding. */
  file: string;
  /** An archive-entry LABEL ("[entry N: name withheld]" when the name itself has a finding). */
  entry: string;
  offset: number; layer: string;
  /** The nearest field/header/param NAME, never the value. */
  name: string;
  /** Keyed digest, comparable only within one report. */
  digest: string; count: number;
}
export interface AuditProblem { problem: string; file: string; entry: string }
export interface AuditReport {
  auditor: string; digestScope: string;
  scanned: { files: number; bytes: number; archiveEntries: number };
  hits: AuditHit[]; problems: AuditProblem[]; refused: boolean;
}
export interface AuditLimits { archiveDepth?: number; base64Depth?: number; normalize?: number; base64Decodes?: number }
export interface AuditOptions { registry?: string[]; limits?: AuditLimits }
export const AUDITOR: string;
export const LIMITS: Readonly<Required<AuditLimits>>;
export function auditPaths(paths: string[], opts?: AuditOptions & { root?: string }): AuditReport;
export function auditBuffer(buf: Buffer, name: string, opts?: AuditOptions): AuditReport;
export function auditedFiles(report: AuditReport): Map<string, { abs: string; rel: string; sha256?: string }>;
export function textSpans(buf: Buffer, min?: number): { text: string; offsetOf(i: number): number };
export function quarantineAll(report: AuditReport, root: string, dir: string): { moved: string[]; failed: string[] };
export function quarantine(report: AuditReport, root: string, dir: string): string[];
