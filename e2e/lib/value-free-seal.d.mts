/** HR-JRN-01: the seal of a value-free publish directory (value-free-seal.mjs). */
export const MANIFEST: string;
export const REFUSED: string;
export const PUBLISH_FORMAT: string;
export const REFUSAL_FORMAT: string;
export interface TreeEntry { path: string; type: 'dir' | 'file' | 'symlink' | 'special'; bytes?: number; nlink?: number }
export function refusalRecordText(reasons: object[]): string;
export function listTree(dir: string): TreeEntry[];
export function writeManifest(dir: string, extra?: Record<string, unknown>): void;
export function verifySeal(dir: string): string | null;
export function isPublishDir(dir: string): boolean;
export function refusalReasons(dir: string): Record<string, unknown>[];
export function removeTree(dir: string): void;
export function stagingFor(dir: string, what: string): string;
export function writeRefusalDir(dir: string, text: string): void;
export function withholdDir(dir: string): string;
/** S1: `p` resolved: its deepest existing ancestor's real path and the rest as spelled. */
export function resolvedPath(p: string): string;
/** The root, the working directory or home, or an ancestor of one, as spelled or resolved: never moved. */
export function isProtectedPath(dir: string): boolean;
/** Whether the module at `url` is the program Node runs, reached through a link or not. */
export function isMainModule(url: string, argv1?: string): boolean;
export type SealStatus = 'published' | 'refused' | 'missing' | 'withheld' | 'untouched';
export function checkBeforePull(dir: string): { code: 0 | 1; status: SealStatus; line: string };
/** The walk consumer's transfer. Only an intact publication or refusal record is copied to `exportDir`;
 * only to a name shorter than VALUE_FREE_MIN_LENGTH (value-free by construction). */
export function exportForPull(dir: string, exportDir: string): { code: 0 | 1; exported: boolean; line: string };
