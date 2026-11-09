/** HR-JRN-01 round 7: the one reading of what value-free mode publishes (value-free-reading.mjs). */
import type { RecordLayer } from './value-free-record.mjs';
export const VALUE_FREE_MIN_LENGTH: number;
export interface ZipEntry { name: string; data: Buffer }
export function isZip(buf: Buffer): boolean;
export function readZip(buf: Buffer): ZipEntry[] | null;
export function writeZip(entries: ZipEntry[]): Buffer;
export function asText(buf: Buffer): string | null;
export const SHAPES: readonly (readonly [string, RegExp])[];
export const RUN: readonly [string, RegExp];
export function kindOf(raw: string): string;
export function stripAnsi(text: string): string;
/** R2 (S2): the runner log's closed schema (value-free-record.mjs checkLog), as written and with colours removed. */
export function logProblem(text: string): 'unknown-kind' | null;
export const NESTING_BOUND: number;
export type Place = 'content' | 'name' | 'zip-entry' | 'zip-entry-name' | 'embedded-zip' | 'base64-field' | 'nested-json';
export type ProblemCode = 'nesting-bound' | 'unsupported-encoding' | 'unreadable-zip' | 'unknown-field' | 'unknown-kind';
/** Round 12 (S2): what a published file is as a record (value-free-record.mjs recordLayer); null for a payload. */
export type { RecordLayer } from './value-free-record.mjs';
export interface Problem { problem: ProblemCode; place: Place; depth: number }
/** What the scan finds: a registered value (its kind and length), or a problem, with where by category only. */
export type Finding = { reason: 'scan-hit'; kind: string; length: number; place: Place } |
  { reason: ProblemCode; place: Place; depth: number };
export const EMBEDDED_ZIP: RegExp;
export function looksJson(s: string): boolean;
export function parseJson(s: string): unknown;
export function isDoc(v: unknown): v is object;
export function isJsonReport(doc: unknown): boolean;
export function isEncodedField(key: string, o: Record<string, unknown>, reportBodies: boolean): boolean;
export function strictBase64(s: string): Buffer | null;
export class Scan {
  readonly findings: Map<string, Finding>;
  constructor(values: string[]);
  /** A text read as it is (a path component, a name), no layer decoded. */
  check(text: string, place: Place): void;
  /** A file's or a text's bytes, every layer decoded. */
  bytes(buf: Buffer, depth?: number, place?: Place, layer?: RecordLayer | null): void;
}
