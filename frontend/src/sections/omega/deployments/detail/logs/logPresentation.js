/**
 * Build-log lines a person does not need to read: Docker's own plumbing, from
 * the legacy builder and from BuildKit's plain progress output, which is what a
 * host with buildx prints (the cloud rig, 2026-10-20: 21 of a deploy's 28 lines).
 * What stays: the deploy's own [deploy] and [checks] lines, every line a build
 * step prints (`#6 0.512 added 12 packages`), and every error (`#6 ERROR: ...`,
 * `ERROR: failed to solve`, the `------` context block).
 */
const LEGACY_BUILDER = [
  /^\s*DEPRECATED:\s+The legacy builder is deprecated\b/i,
  /^\s*Install the buildx component to build images with BuildKit:?\s*$/i,
  /^\s*https:\/\/docs\.docker\.com\/go\/buildx\/?\s*$/i,
  /^\s*Sending build context to Docker daemon\b/i,
  /^\s*Step\s+\d+\/\d+\s*:/i,
  /^\s*--->\s+Removed intermediate container\b/i,
  /^\s*Removing intermediate container\s+[0-9a-f]{8,64}\s*$/i,
  /^\s*--->\s+Running in\s+[0-9a-f]{8,64}\s*$/i,
  /^\s*--->\s+Using cache\s*$/i,
  /^\s*--->\s+[0-9a-f]{8,64}\s*$/i,
  /^\s*Successfully built\s+[0-9a-f]{8,64}\s*$/i,
  /^\s*Successfully tagged\s+\S+\s*$/i,
];

// BuildKit numbers each vertex (`#7 ...`). Only its bookkeeping is hidden; a
// vertex's own output carries a timestamp (`#7 0.512 ...`) and is never matched.
const BUILDKIT = [
  /^#\d+\s+building with\s/i,
  /^#\d+\s+\[internal\]\s/i,
  /^#\d+\s+\[[^\]]*\d+\/\d+\]\s/i,
  /^#\d+\s+transferring\s+(dockerfile|context)\b/i,
  /^#\d+\s+(DONE|CACHED)(\s+[\d.]+s)?\s*$/i,
  /^#\d+\s+exporting\s/i,
  /^#\d+\s+writing image\s+sha256:/i,
  /^#\d+\s+naming to\s/i,
  /^#\d+\s+unpacking to\s/i,
  /^#\d+\s+resolve\s/i,
  /^#\d+\s+(extracting\s+)?sha256:[0-9a-f]{12,}/i,
];

const BUILD_NOISE = Object.freeze([...LEGACY_BUILDER, ...BUILDKIT]);

export function isBuildNoise(line) {
  const text = String(line ?? "");
  return BUILD_NOISE.some((pattern) => pattern.test(text));
}

export function visibleLogEntries(lines, source) {
  if (source !== "build") return { entries: lines, hiddenCount: 0 };
  const entries = lines.filter((entry) => !isBuildNoise(entry.line));
  return { entries, hiddenCount: lines.length - entries.length };
}
