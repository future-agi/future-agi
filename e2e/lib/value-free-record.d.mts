/** HR-JRN-01 round 12 (the structural review's S2): the closed record schema of what value-free mode publishes as a report
 * record (value-free-record.mjs). */
export const PUBLISH_FORMAT: string;
export const REFUSAL_FORMAT: string;
export const UNKNOWN_FIELD: 'unknown-field';
export const UNKNOWN_KIND: 'unknown-kind';
export type RecordProblem = 'unknown-field' | 'unknown-kind';
/** What a published file (or a record zip's entry) is: one record of a type, one per line, a zip or a text with embedded
 * zips whose entries are classified by a layout, the runner log (R2: lines of text), or a record of no known kind. */
export interface RecordLayer { doc?: string; lines?: string; zip?: string; embedded?: string; log?: string; unknown?: boolean }
/** R2 (S2): runner.log as a record: lines of text, read against checkLog. */
export const RUNNER_LOG: Readonly<RecordLayer>;
/** R2 (S2): the runner log's closed schema: null when it holds only lines of text, else the refusal code (a document in it:
 * a JSON object or array from the start of a line to the end of one, or an embedded zip). */
export function checkLog(text: string): 'unknown-kind' | null;
export const RECORDS: Readonly<Record<string, unknown>>;
export function projectRecord(doc: unknown, recordType: string): { value: unknown; problem?: undefined } | { problem: RecordProblem };
export function checkRecord(doc: unknown, recordType: string): RecordProblem | null;
export function recordLayer(rel: string): RecordLayer | null;
export function entryLayer(layout: string, name: string): RecordLayer | null;
