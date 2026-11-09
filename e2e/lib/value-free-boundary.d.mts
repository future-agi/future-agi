/** HR-JRN-01 round 8: the publication choke point of value-free mode (value-free-boundary.mjs). */
import type { Scan } from './value-free-reading.mjs';

/** Everything the choke point prints of its own, once: each shorter than VALUE_FREE_MIN_LENGTH bytes. */
export const STATUS: Readonly<{ clean: string; record: string; registry: string; value: string; entry: string; seal: string;
  failed: string }>;
/** Every registered value, or null when the registries cannot be read whole (never an empty list for that). */
export function registeredValues(dir: string): string[] | null;
export function patternRoot(pattern: string): string | null;
/** Round 12 (S1): where a pull's entry canonically lies, or null when a link below the pull root lies on its way. */
export function canonicalEntry(root: string, rootC: string | null, rel: string): string | null;
/** Round 10: the walk launcher's check before dispatch; true when any pull is not one exact path below the pull root. */
export function declarationRefused(pulls: string[]): boolean;
/** A transferred path as the scan reads it: every component, and the whole relative path. */
export function checkPath(scan: Scan, rel: string): void;
export function emittedBytes(held: Buffer | null, status: string): Buffer;
export function emit(buf: Buffer): void;
export interface Verdict { code: 0 | 1; out: Buffer; side?: Buffer[] }
export function finalCheck(input: { root: string; registries: string; held?: string; pulls: string[]; side?: Buffer[];
  ok?: { status: string; code: 0 | 1 } }): Verdict;
export function ciCheck(input: { registries: string; held: string; publish: string; githubOutput: string }): Verdict;
export function sealCheck(dir: string, exportDir?: string): Verdict;
export function run(args: string[]): Verdict;
