// The landing gate's SHAPE check of an auditor report (708 add.78 r2), run
// before a report is allowed to land (evidence-audit.mjs --check-report, with
// a fresh audit of the report's bytes on top). A report may carry only the
// keys this auditor writes, and every string only the shape it should have:
// kinds and problem codes, 12-hex digests, field names (detect.mjs safeName),
// printable labels, the launch's globs and nonce. Anything else is a reason;
// reasons name the key, never its value.
const KIND = /^[a-z][a-z0-9-]{0,39}$/;
const DIGEST = /^[0-9a-f]{12}$/;
const FIELD = /^[A-Za-z0-9_.@-]{0,48}$/;
const LABEL = /^[\x20-\x7e]{0,1024}$/;
const LAYER = /^[A-Za-z0-9_.@>+-]{1,512}$/;
const GLOB = /^[A-Za-z0-9_.*?/-]{1,256}$/;
const NONCE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

const TOP = new Set([
  "auditor",
  "digestScope",
  "scanned",
  "hits",
  "problems",
  "refused",
  "registryUsed",
  "inputs",
  "nonce",
  "quarantined",
  "quarantineFailed",
  "stage",
]);

const isObject = (v) =>
  v !== null && typeof v === "object" && !Array.isArray(v);
const count = (v) => Number.isInteger(v) && v >= 0;

function keysOnly(obj, allowed, where, bad) {
  for (const k of Object.keys(obj))
    if (!allowed.includes(k)) bad.push(`${where}: unexpected key`);
}

/** Reasons the report may not land ([] when it may). */
export function checkReport(r) {
  const bad = [];
  if (!isObject(r)) return ["report: not an object"];
  for (const k of Object.keys(r))
    if (!TOP.has(k)) bad.push("report: unexpected key");
  if (r.auditor !== "e2e/scripts/evidence-audit") bad.push("auditor");
  if (typeof r.digestScope !== "string" || !LABEL.test(r.digestScope))
    bad.push("digestScope");
  if (typeof r.refused !== "boolean") bad.push("refused");
  if (!isObject(r.scanned)) bad.push("scanned");
  else {
    keysOnly(r.scanned, ["files", "bytes", "archiveEntries"], "scanned", bad);
    for (const k of ["files", "bytes", "archiveEntries"])
      if (!count(r.scanned[k])) bad.push(`scanned.${k}`);
  }
  if (!Array.isArray(r.hits)) bad.push("hits");
  else
    for (const h of r.hits) {
      if (!isObject(h)) {
        bad.push("hits[]");
        continue;
      }
      keysOnly(
        h,
        ["kind", "file", "entry", "offset", "layer", "name", "digest", "count"],
        "hits[]",
        bad,
      );
      if (!KIND.test(h.kind ?? "")) bad.push("hits[].kind");
      if (typeof h.file !== "string" || !LABEL.test(h.file))
        bad.push("hits[].file");
      if (typeof h.entry !== "string" || !LABEL.test(h.entry))
        bad.push("hits[].entry");
      if (!count(h.offset)) bad.push("hits[].offset");
      if (typeof h.layer !== "string" || !LAYER.test(h.layer))
        bad.push("hits[].layer");
      if (typeof h.name !== "string" || !FIELD.test(h.name))
        bad.push("hits[].name");
      if (!DIGEST.test(h.digest ?? "")) bad.push("hits[].digest");
      if (!count(h.count) || h.count < 1) bad.push("hits[].count");
    }
  if (!Array.isArray(r.problems)) bad.push("problems");
  else
    for (const p of r.problems) {
      if (!isObject(p)) {
        bad.push("problems[]");
        continue;
      }
      keysOnly(p, ["problem", "file", "entry"], "problems[]", bad);
      if (!KIND.test(p.problem ?? "")) bad.push("problems[].problem");
      if (typeof p.file !== "string" || !LABEL.test(p.file))
        bad.push("problems[].file");
      if (typeof p.entry !== "string" || !LABEL.test(p.entry))
        bad.push("problems[].entry");
    }
  if (r.refused !== (r.hits?.length > 0 || r.problems?.length > 0))
    bad.push("refused disagrees with hits/problems");
  if ("registryUsed" in r && typeof r.registryUsed !== "boolean")
    bad.push("registryUsed");
  if ("inputs" in r) {
    if (!isObject(r.inputs)) bad.push("inputs");
    else {
      keysOnly(r.inputs, ["paths", "globs", "files"], "inputs", bad);
      if (!count(r.inputs.paths) || !count(r.inputs.files))
        bad.push("inputs counts");
      if (
        !Array.isArray(r.inputs.globs) ||
        r.inputs.globs.some((g) => typeof g !== "string" || !GLOB.test(g))
      )
        bad.push("inputs.globs");
    }
  }
  if ("nonce" in r && (typeof r.nonce !== "string" || !NONCE.test(r.nonce)))
    bad.push("nonce");
  for (const k of ["quarantined", "quarantineFailed"])
    if (
      k in r &&
      (!Array.isArray(r[k]) ||
        r[k].some((l) => typeof l !== "string" || !LABEL.test(l)))
    )
      bad.push(k);
  if ("stage" in r) {
    if (!isObject(r.stage)) bad.push("stage");
    else {
      keysOnly(r.stage, ["files", "failed", "complete"], "stage", bad);
      if (
        !count(r.stage.files) ||
        !count(r.stage.failed) ||
        typeof r.stage.complete !== "boolean"
      )
        bad.push("stage fields");
    }
  }
  return bad;
}

/**
 * What a verified report allows to land:
 *   clean       nothing found (and, when staged, every clean file staged)
 *   refused     findings, every refused file quarantined, the rest staged
 *   incomplete  findings with a quarantine or staging that did not complete
 */
export function landingState(r) {
  const staged = !("stage" in r) || r.stage.complete === true;
  const quarantined = (r.quarantineFailed ?? []).length === 0;
  if (!r.refused) return staged ? "clean" : "incomplete";
  return quarantined && staged && "quarantined" in r ? "refused" : "incomplete";
}
