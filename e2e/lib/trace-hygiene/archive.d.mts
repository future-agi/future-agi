export const MARKER: string;
export function isTraceArchive(entries: { name: string }[]): boolean;
export function isScrubbed(entries: { name: string }[]): boolean;
/** Scrub trace archives and in-memory runner events (mutated in place) as one set. */
export function scrubTraceSet(buffers: Buffer[], runnerEvents?: unknown[]): {
  buffers: Buffer[]; stats: Record<string, number>;
};
/** Scrub one trace archive in place; false when it is not a trace or is already scrubbed. */
export function scrubTraceFile(file: string, opts?: { force?: boolean }): boolean;
export function sweepTraceArchives(dirs: string[], opts?: { force?: boolean }): { scrubbed: string[]; failed: string[] };
