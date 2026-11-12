/** Types for @omega/hub (plain ESM in src/; this file is hand-written). */

export type HubKind = "app" | "agent" | "team" | "skill" | "plugin" | "mcp" | "workflow";
export type HubColor = "red" | "yellow" | "green" | "blue" | "indigo" | "purple" | "pink" | "gray";
export type HubKeyless = "scripted" | "recorded" | "echo" | "none";
export type HubRuleId = "R1" | "R2" | "R3" | "R4" | "R5" | "R6" | "R7" | "R8" | "R9" | "R10" | "R11" | "R12" | "R13" | "R14" | "R15" | "P1" | "P2" | "P3" | "P4" | "P5";

export interface HubLiveModel {
  readonly model: string;
  readonly keys: readonly string[];
  readonly checked?: string;
}

export interface HubKey {
  readonly name: string;
  readonly why: string;
  readonly get: string;
  readonly for: "live" | "service";
  readonly secret: boolean;
}

export interface HubManifest {
  readonly $schema?: string;
  readonly schema: "omega-hub/v1";
  readonly id: string;
  readonly title: string;
  readonly tagline: string;
  readonly kind: HubKind;
  readonly cover: { readonly emoji: string; readonly from: HubColor; readonly to: HubColor };
  readonly author: { readonly github: string; readonly name?: string; readonly kind: "maintainer" | "community" };
  readonly tags: readonly string[];
  readonly difficulty: "starter" | "intermediate" | "advanced";
  readonly license: string;
  readonly added: string;
  readonly remix_of?: string;
  readonly models: { readonly keyless: HubKeyless; readonly live?: readonly HubLiveModel[]; readonly maxCalls?: number };
  readonly keys?: readonly HubKey[];
  readonly requires?: { readonly node?: string; readonly bins?: readonly string[]; readonly playwright?: boolean };
  readonly run: { readonly keyless: readonly string[]; readonly live?: readonly string[] };
  readonly test: string;
  readonly demo: { readonly cast: string; readonly redact?: readonly string[]; readonly caption?: string };
  readonly install?: { readonly plugin?: string; readonly skillpack?: string; readonly mcp?: { readonly name: string; readonly command: readonly string[] } };
  readonly source?: string;
  readonly files?: readonly string[];
  readonly featured?: { readonly week: string; readonly why: string };
  readonly hidden?: boolean;
}

export interface HubFinding {
  readonly rule: HubRuleId | string;
  readonly code: string;
  readonly file: string;
  readonly message: string;
  readonly fix: string;
}

export interface HubWalkedFile {
  readonly rel: string;
  readonly abs: string;
  readonly bytes: number;
}

export interface HubWalk {
  readonly files: HubWalkedFile[];
  readonly symlinks: string[];
  readonly nodeModules: string[];
}

export interface HubEntryResult {
  readonly id: string;
  readonly manifest: HubManifest | null;
  readonly findings: HubFinding[];
  readonly imports: string[];
}

export interface HubValidateOptions {
  readonly today?: string;
  readonly ids?: readonly string[];
  readonly hubDir?: string;
  readonly where?: string;
  readonly words?: readonly (readonly [string, RegExp])[];
}

export interface HubCatalogFile {
  readonly path: string;
  readonly bytes: number;
  readonly lines: number;
  readonly lang: string;
}

export interface HubCatalogEntry {
  readonly id: string;
  readonly title: string;
  readonly tagline: string;
  readonly kind: HubKind;
  readonly cover: HubManifest["cover"];
  readonly author: HubManifest["author"];
  readonly tags: readonly string[];
  readonly difficulty: HubManifest["difficulty"];
  readonly license: string;
  readonly added: string;
  readonly remix_of: string | null;
  readonly remixes: readonly string[];
  readonly models: { readonly keyless: HubKeyless; readonly live: readonly (HubLiveModel & { readonly checked: string | null })[]; readonly maxCalls: number | null };
  readonly keys: readonly HubKey[];
  readonly requires: NonNullable<HubManifest["requires"]>;
  readonly run: { readonly keyless: readonly string[]; readonly live: readonly string[] };
  readonly test: string;
  readonly install: HubManifest["install"] | null;
  readonly featured: HubManifest["featured"] | null;
  readonly imports: readonly string[];
  readonly path: string;
  readonly filesRoot: string;
  readonly files: readonly HubCatalogFile[];
  readonly digest: string;
  readonly cast: { readonly path: string; readonly lines: number; readonly caption: string } | null;
}

export interface HubCatalog {
  readonly schema: "omega-hub-catalog/v1";
  readonly repo: string;
  readonly commit: string;
  readonly entries: readonly HubCatalogEntry[];
}

export interface HubParsedCast {
  readonly header: { readonly version: 2; readonly width: number; readonly height: number; readonly title?: string };
  readonly events: readonly [number, string, string][];
}

export const SCHEMA: "omega-hub/v1";
export const CATALOG_SCHEMA: "omega-hub-catalog/v1";
export const LOCK_SCHEMA: "omega-hub-lock/v1";
export const MANIFEST_FILE: "omega.hub.json";
export const README_FILE: "README.md";
export const ENV_EXAMPLE_FILE: ".env.example";
export const HUB_DIR: "hub";
export const REPO: string;
export const MAINTAINER_ACCOUNT: string;
export const PLACEHOLDER_AUTHOR: string;
export const KINDS: readonly HubKind[];
export const ID: RegExp;
export const TAG: RegExp;
export const MODEL: RegExp;
export const KEY_NAME: RegExp;
export const COLORS: readonly HubColor[];
export const KEYLESS: readonly HubKeyless[];
export const LICENSES: readonly string[];
export const DEFAULT_CAPTIONS: Readonly<Record<HubKeyless, string>>;
export const LABELS: { readonly onBehalf: string; readonly featured: string; readonly moderation: string; readonly maintainer: string };
export const RULES: Readonly<Record<string, string>>;
export const LIMITS: Readonly<Record<string, number>>;
export const README_SECTIONS: readonly string[];
export const TEMPLATE_ROOT: string;
export const SECRET_PATTERNS: readonly (readonly [string, RegExp])[];

export function todayUtc(now?: Date): string;
export function isCalendarDate(value: unknown): boolean;
export function finding(rule: string, file: string, message: string, fix?: string): HubFinding;

export function readManifest(entryDir: string, file?: string): { manifest: HubManifest | null; findings: HubFinding[] };
export function checkShape(manifest: object, file: string): HubFinding[];
export function isRelativePath(value: unknown): boolean;

export function walk(root: string): HubWalk;
export function runEntryFile(argv: readonly string[] | undefined): string | null;
export function parseEnvExample(text: string): { names: string[]; valued: string[] };
export function specifiersOf(source: string): string[];
export function digestFiles(files: readonly HubWalkedFile[]): string;
export function copyableFiles(walked: HubWalk, castRel: string): HubWalkedFile[];
export function langOf(rel: string): string;

export function readProseRules(repoRoot: string): [string, RegExp][];
export function wordMatchers(list: readonly string[]): [string, RegExp][];
export function firstSection(markdown: string): string;
export function h2Headings(markdown: string): string[];

export function parseCast(text: string): HubParsedCast;
export function scrubText(text: string, options?: { entryDirs?: readonly string[]; tmpDirs?: readonly string[]; home?: string }): string;
export function redactText(text: string, patterns?: readonly string[]): string;
export function normalizeOutput(text: string): string;
export function castOutput(parsed: HubParsedCast): string;
export function writeCast(options: { id: string; chunks: readonly { at?: number; text: string }[] }): string;
export function checkCast(text: string, file: string): HubFinding[];
export function compareOutputs(recorded: string, fresh: string): { line: number; expected: string; actual: string } | null;
export function tailLines(parsed: HubParsedCast, count?: number): string[];

export function entryIds(repoRoot: string, hubDir?: string): string[];
export function filesRootOf(repoRoot: string, entryDir: string, manifest: HubManifest | null): string;
export function validateEntry(repoRoot: string, entryDir: string, options?: Omit<HubValidateOptions, "ids"> & { ids?: ReadonlySet<string> }): HubEntryResult;
export function validateAll(repoRoot: string, options?: HubValidateOptions): { entries: HubEntryResult[]; findings: HubFinding[] };

export function buildCatalog(repoRoot: string, options?: { commit?: string; repo?: string; hubDir?: string }): HubCatalog;
export function catalogEntry(repoRoot: string, entryDir: string, manifest: HubManifest, remixes?: readonly string[]): HubCatalogEntry;
export function searchCatalog(entries: readonly HubCatalogEntry[], words: string): HubCatalogEntry[];

export function checkPullRequest(
  pr: { changed?: readonly string[]; author?: string; labels?: readonly string[] },
  now: (id: string) => HubManifest | null,
  before: (id: string) => HubManifest | null,
  hubDir?: string
): HubFinding[];
export function changedIds(repoRoot: string, base: string, hubDir?: string): string[];
export function manifestAt(repoRoot: string, ref: string, id: string, hubDir?: string): HubManifest | null;

export function readKinds(templateRoot?: string): Record<HubKind, { folder: string; title: string; tagline: string; emoji: string; from: HubColor; to: HubColor; tags: string[] }>;
export function scaffoldFiles(options: { id: string; kind?: HubKind; author?: string; added?: string }, templateRoot?: string): { rel: string; content: string }[];
export function remixFiles(sourceDir: string, options: { id: string; author?: string; added?: string }): { rel: string; content: string }[];
