// Single owner of path enumeration + resolution against a row-detail object.
// Mirrors the BE mapping resolvers (tracer/utils/eval.py): soft-flattened
// span_attributes paths, spans/traces collections addressed by index.

import {
  canonicalEntries,
  canonicalKeys,
  stripAttributePathPrefix,
} from "src/utils/utils";

export const EAGER_DEPTH = 4;
export const ARRAY_PEEK = 500;
export const DICT_LIMIT = 5000;

// Subtrees never recursed into — the key stays selectable but its (often
// multi-MB) children are skipped. raw_log is the Vapi call dump;
// metrics_data / call_logs are per-turn payloads; provider_transcript is
// the raw transcript string.
export const NO_RECURSE_KEYS = new Set([
  "raw_log",
  "rawLog",
  "metrics_data",
  "metricsData",
  "call_logs",
  "callLogs",
  "provider_transcript",
  "providerTranscript",
]);

const isObjectLike = (v) => v !== null && typeof v === "object";

// Wrapper keys that stripAttributePathPrefix removes from surfaced paths.
// They must not consume a depth level either — otherwise the visible
// (stripped) path is shorter than the depth budget it was charged.
const FREE_KEYS = new Set(["span_attributes", "spanAttributes"]);

// depthUsed counts NAMED segments only; numeric array indices and stripped
// wrapper keys (FREE_KEYS) ride free.
function walkNode(node, prefix, depthUsed, maxDepth, rawPaths, rawTruncated) {
  if (Array.isArray(node)) {
    node.slice(0, ARRAY_PEEK).forEach((item, idx) => {
      const path = prefix ? `${prefix}.${idx}` : String(idx);
      rawPaths.push(path);
      if (!isObjectLike(item)) return;
      if (depthUsed >= maxDepth) {
        rawTruncated.add(path);
        return;
      }
      walkNode(item, path, depthUsed, maxDepth, rawPaths, rawTruncated);
    });
    return;
  }
  for (const [k, v] of canonicalEntries(node)) {
    if (k.startsWith("_")) continue;
    const path = prefix ? `${prefix}.${k}` : k;
    rawPaths.push(path);
    if (!isObjectLike(v)) continue;
    if (NO_RECURSE_KEYS.has(k)) continue;
    if (!Array.isArray(v) && canonicalKeys(v).length >= DICT_LIMIT) continue;
    const nextDepth = FREE_KEYS.has(k) ? depthUsed : depthUsed + 1;
    if (nextDepth >= maxDepth && !Array.isArray(v)) {
      rawTruncated.add(path);
      continue;
    }
    // Arrays at the boundary still enumerate indices (free); their object
    // items get truncated inside the array branch above.
    walkNode(v, path, nextDepth, maxDepth, rawPaths, rawTruncated);
  }
}

// Soft-flatten (strip span_attributes prefixes) + dedupe, top-level wins.
function flattenAndDedupe(rawPaths, rawTruncated) {
  const seen = new Set();
  const paths = [];
  rawPaths.forEach((p) => {
    const short = stripAttributePathPrefix(p);
    if (seen.has(short)) return;
    seen.add(short);
    paths.push(short);
  });
  const truncated = new Set();
  rawTruncated.forEach((p) => truncated.add(stripAttributePathPrefix(p)));
  return { paths, truncated };
}

export function walkPaths(root, { maxDepth = EAGER_DEPTH } = {}) {
  if (!isObjectLike(root)) return { paths: [], truncated: new Set() };
  const rawPaths = [];
  const rawTruncated = new Set();
  walkNode(root, "", 0, maxDepth, rawPaths, rawTruncated);
  return flattenAndDedupe(rawPaths, rawTruncated);
}

// Walk a dotted path with backtracking. span_attributes bags are flat maps
// whose keys are themselves dotted ("metadata.deep_object"), so a pure
// segment-by-segment descent can never re-find the atomic key walkNode
// enumerated through. Mirror the BE resolver (_resolve_attr): at every
// object node try the longest literal dotted-key join first, backtrack to
// shorter joins, then retry the whole tail inside node.span_attributes.
// Returns { found: boolean, value, unknown: boolean }.
const NOT_FOUND = { found: false, unknown: false };

function descendSegments(node, segments) {
  if (!segments.length) return { found: true, value: node, unknown: false };
  if (!isObjectLike(node)) return NOT_FOUND;
  if (Array.isArray(node)) {
    const idx = Number(segments[0]);
    if (!Number.isInteger(idx) || idx < 0 || idx >= node.length) {
      return NOT_FOUND;
    }
    return descendSegments(node[idx], segments.slice(1));
  }
  // Descending into a collection whose contents were never fetched
  // (session traces beyond the first) is unknowable, not missing.
  if (segments[0] === "spans" && node._spansLoaded === false) {
    return { found: false, unknown: true };
  }
  const entries = Object.fromEntries(canonicalEntries(node));
  for (let k = segments.length; k >= 1; k -= 1) {
    const key = segments.slice(0, k).join(".");
    if (key in entries) {
      const res = descendSegments(entries[key], segments.slice(k));
      if (res.found || res.unknown) return res;
    }
  }
  const attrs = entries.span_attributes ?? entries.spanAttributes;
  if (isObjectLike(attrs) && !Array.isArray(attrs) && attrs !== node) {
    return descendSegments(attrs, segments);
  }
  return NOT_FOUND;
}

function descend(root, path) {
  return descendSegments(root, path.split("."));
}

export function expandPaths(root, prefixPath, { maxDepth = EAGER_DEPTH } = {}) {
  const target = descend(root, prefixPath);
  if (!target.found || !isObjectLike(target.value)) {
    return { paths: [], truncated: new Set() };
  }
  const rawPaths = [];
  const rawTruncated = new Set();
  walkNode(target.value, prefixPath, 0, maxDepth, rawPaths, rawTruncated);
  return flattenAndDedupe(rawPaths, rawTruncated);
}

export function resolvePath(root, path) {
  if (!isObjectLike(root) || !path) return { status: "missing" };
  const result = descend(root, path);
  if (result.found) return { status: "resolved", value: result.value };
  return { status: result.unknown ? "unknown" : "missing" };
}

// BE parity: _resolve_trace_path orders spans.<n> by (start_time, id) with
// null start_times last. The preview must show spans at the index the BE
// will resolve.
export function sortSpansForMapping(spans) {
  return [...(spans || [])].sort((a, b) => {
    const an = a?.start_time == null;
    const bn = b?.start_time == null;
    if (an !== bn) return an ? 1 : -1;
    if (!an && a.start_time !== b.start_time) {
      return a.start_time < b.start_time ? -1 : 1;
    }
    return String(a?.id) < String(b?.id) ? -1 : 1;
  });
}
