// Test-only wiring declarations. P0 pins stay immutable; lanes append full
// ordered lists, including repetitions. The latest entry is the sole active
// pin for a file and must preserve every earlier entry as a subsequence.
import { LAUNCH_SURFACES } from "../launchSurfaces";
import splits from "../__fixtures__/registry-splits-705.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import wiringAdditions from "../__fixtures__/registry-wiring-additions-705.json";
import { resolveCapOwners } from "./registryCapOwners";
import { resolvePathOwners } from "./registryPathOwners";
import { resolveSettingOwners } from "./registrySettingOwners";

const FIELDS = new Set(["file", "lane", "feature", "expected", "newSite"]);
// Ownership moves share the fixture; each kind has its own resolver.
const OWNER_KINDS = new Set(["capOwner", "settingOwner", "pathOwner"]);
const RETIRED = new Set(
  Object.entries(splits)
    .filter(([id, children]) => !children.includes(id))
    .map(([id]) => id),
);
const text = (value) => typeof value === "string" && value.trim().length > 0;
const relativeFile = (file) =>
  text(file) &&
  !file.includes("\\") &&
  file.split("/").every((part) => part && part !== "." && part !== "..");
const fail = (code, file) => {
  throw new Error(`Wiring addition ${code}: ${file ?? "<unknown>"}`);
};

function isSubsequence(previous, next) {
  let index = 0;
  for (const id of next)
    if (index < previous.length && id === previous[index]) index += 1;
  return index === previous.length;
}

// Keep the original consumer matcher: pins cover every literal read, in source
// order. Equality against the resolved list still catches undeclared gates.
export function literalSurfaceReads(source) {
  return [...source.matchAll(/isSurfaceShown\(\s*["']([^"']+)["']\s*\)/g)].map(
    (match) => match[1],
  );
}

/**
 * Entries are an append-only history, not competing active overrides.
 * newSite:true is required on every entry for a file outside the P0 sites;
 * it is forbidden on P0 files. Reading the resolved sites also requires every
 * newly pinned source file to exist. These declarations never activate gates.
 * Owner entries (capOwner, settingOwner, pathOwner) are validated by their
 * resolvers here too, never silently skipped.
 */
export function resolveWiringSites(
  sites,
  entries = wiringAdditions,
  { surfaces = LAUNCH_SURFACES, laneDeclarations = laneAdditions } = {},
) {
  if (!Array.isArray(entries)) fail("not-list");
  for (const resolveOwners of [
    resolveCapOwners,
    resolveSettingOwners,
    resolvePathOwners,
  ])
    resolveOwners(entries, { laneDeclarations, surfaces });
  const baseline = new Map(sites);
  const active = new Map(
    sites.map(([file, expected]) => [file, [...expected]]),
  );
  const registered = new Set([
    ...surfaces.map((surface) => surface.id),
    ...laneDeclarations.map((entry) => entry.id),
  ]);
  for (const entry of entries) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry))
      fail("not-entry");
    if (OWNER_KINDS.has(entry.kind)) continue;
    const { file, lane, feature, expected, newSite } = entry;
    for (const key of Object.keys(entry))
      if (!FIELDS.has(key)) fail("unknown-field", file);
    if (!relativeFile(file)) fail("invalid-file", file);
    if (!text(lane)) fail("invalid-lane", file);
    if (!text(feature)) fail("invalid-feature", file);
    if (newSite !== undefined && newSite !== true)
      fail("invalid-new-site", file);
    if (baseline.has(file) && newSite) fail("existing-site", file);
    if (!baseline.has(file) && !newSite) fail("unknown-site", file);
    if (!Array.isArray(expected) || expected.some((id) => !text(id)))
      fail("invalid-expected", file);
    if (!isSubsequence(baseline.get(file) ?? [], expected))
      fail("p0-order", file);
    if (!isSubsequence(active.get(file) ?? [], expected))
      fail("history-order", file);
    for (const id of expected) {
      if (RETIRED.has(id)) fail("retired-id", file);
      if (id.startsWith("exp.results.")) fail("results-id", file);
      if (!registered.has(id)) fail("unknown-id", file);
    }
    active.set(file, [...expected]);
  }
  return [...active];
}
