// HR-JRN-01 round 5: the whole vocabulary of a value-free refusal record (REFUSED.json,
// lib/value-free-seal.mjs). A reason is `reason`, one of REASON_CODES, plus any of the
// fields VOCABULARY lists, each a fixed code, kind or category, a length, a depth, or a
// file:line. Nothing else is representable: never a value, a path, a free label or a
// location string. The reporter (lib/value-free-publish.ts) cuts every reason to this
// before it scans the record, and the seal check refuses a record that holds anything
// outside it. Plain Node, no dependencies.
export const REASON_CODES = [
  "short-value",
  "token-syntax",
  "unsafe-publish-directory",
  "unsafe-reporter-target",
  "unresolvable-reporter",
  "publish-directory-written-before-assembly",
  "unsafe-source",
  "missing-report",
  "unreadable-report",
  "scan-hit",
  "nesting-bound",
  "unsupported-encoding",
  "unreadable-zip",
  "unsafe-entry",
  "changed-after-assembly",
  "assembly-failed",
  "not-assembled",
  "value-in-publish-path",
  "unknown-field",
  "unknown-kind",
  "unlisted-reason",
];
/** What a token says its value was (lib/value-free.ts kindOf). */
export const VALUE_KINDS = ["email", "id", "key", "jwt", "run", "value"];
/** The labels a registration may name a refused value by (lib/scope-actors.ts); any other label is `value`. */
export const REGISTRATION_KINDS = [
  "value",
  "credential",
  "invite link",
  "invite token",
  "organization id",
  "organization name",
  "user id",
  "email",
  "email local part",
  "user name",
  "workspace id",
  "workspace name",
  "org key",
];
/** Where in a publication: its top-level category, and the layer inside a file. */
export const AREAS = [
  "runner-log",
  "reports",
  "html",
  "attachments",
  "manifest",
  "refusal-record",
  "runner-output",
  "publish-path",
  "other",
];
export const PLACES = [
  "content",
  "name",
  "zip-entry",
  "zip-entry-name",
  "embedded-zip",
  "base64-field",
  "nested-json",
];
const WHY = [
  "it is, or holds, a source tree",
  "it overlaps the publish directory",
  "value-free mode publishes only the built-in reporters and walks/vm/counts-reporter.cjs",
  "it names no output file",
  "it writes into the publish directory",
  "it holds sources",
  "it has a source extension",
  "it exists and is not a publish directory this harness wrote",
  "symlink",
  "hardlink",
  "special file",
  "the run ended before its outputs were assembled",
  "the seal was rewritten after assembly",
  "missing",
  "not a directory",
  "it holds a link or a special file",
  "it holds a hard link",
  "a refusal record with other entries",
  "not a refusal record",
  "no seal",
  "not a seal",
  "a file was added or removed after assembly",
  "a file was added or changed after assembly",
  "a directory was added after assembly",
  "error",
  "it has no closed record schema",
  "it writes its report to the runner output",
];
const VOCABULARY = {
  reason: (v) => REASON_CODES.includes(v),
  kind: (v) =>
    [
      ...VALUE_KINDS,
      ...REGISTRATION_KINDS,
      "symlink",
      "special",
      "hardlink",
    ].includes(v),
  reporter: (v) =>
    [
      "json",
      "junit",
      "blob",
      "html",
      "counts",
      "custom reporter",
      "project outputDir",
      "value-free",
    ].includes(v),
  source: (v) =>
    [
      "attachment",
      "html report",
      "json",
      "junit",
      "blob",
      "html",
      "counts",
    ].includes(v),
  why: (v) => WHY.includes(v) || /^E[A-Z0-9]{2,20}$/.test(v),
  area: (v) => AREAS.includes(v),
  place: (v) => PLACES.includes(v),
  at: (v) => /^(?:[A-Za-z0-9._-]{1,80}:\d{1,7}|unknown)$/.test(v),
  length: (v) => Number.isInteger(v) && v >= 0 && v < 1e7,
  depth: (v) => Number.isInteger(v) && v >= 0 && v < 1e4,
};
/** Whether a reason is made only of the vocabulary (every field known, every value in its list or pattern). */
export function isCleanReason(r) {
  return (
    Boolean(r) &&
    typeof r === "object" &&
    !Array.isArray(r) &&
    VOCABULARY.reason(r.reason) &&
    Object.entries(r).every(
      ([k, v]) => Object.hasOwn(VOCABULARY, k) && VOCABULARY[k](v),
    )
  );
}
/** A reason cut to the vocabulary: every field that does not fit dropped, an unknown code `unlisted-reason`. */
export function cleanReason(r) {
  const out = {
    reason: VOCABULARY.reason(r?.reason) ? r.reason : "unlisted-reason",
  };
  for (const [k, v] of Object.entries(r ?? {})) {
    if (k !== "reason" && Object.hasOwn(VOCABULARY, k) && VOCABULARY[k](v))
      out[k] = v;
  }
  return out;
}
