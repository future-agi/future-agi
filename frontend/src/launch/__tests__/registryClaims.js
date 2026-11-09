// Registry claim contract (ruling 705) for the eight WORKS slots of
// madeReal.js, measured against the P0 baseline (registry-before-705.json,
// registry-options-before-705.json). registry-705.test.js applies it to the
// real slots; registry-705-claims.test.js to fixture claim sets injected
// through registryClaimSets.js, the way the launch switch reads its claims.
//
// The baseline is immutable. A live WORKS claim may show its own surface
// where the switch applies a claim (under a shown parent, and only with
// experimentsBasic for an experiments surface); a shared host follows its
// parts. Every other surface, option, route and deep-link key keeps its
// baseline.
import before from "../__fixtures__/registry-before-705.json";
import searchKeyOwners from "../__fixtures__/registry-search-key-owners-705.json";
import optionsBefore from "../__fixtures__/registry-options-before-705.json";
import splits from "../__fixtures__/registry-splits-705.json";
import inventory from "../__fixtures__/inventory-classes.json";
import prototypePaths from "../__fixtures__/registry-prototype-paths-705.json";
import {
  LAUNCH_SURFACES,
  SURFACE_BY_ID,
  matchesGlob,
  surfacesOfOption,
} from "../launchSurfaces";
import { resolvePathOwners } from "./registryPathOwners";
import { NEW_SLOTS } from "./registrySlots";

/** The WORKS slots, in madeReal.js order. */
export { NEW_SLOTS };
/** The slots P0 inherited (L1-L6), byte-for-byte. */
export const LEGACY_SLOTS = Object.freeze(Object.keys(before.claims));
const LEGACY_CLAIMS = new Set(Object.values(before.claims).flat());
/** Main's joint Results lane activates these ids (705 addendum 4). */
export const RESULTS_PREFIX = "exp.results.";
/** A two-tenant walk id, as e2e/FLOWS.md headings name it. */
const WALK_ID = /\b[A-Z][A-Z0-9]*-E2E-\d{3}\b/;
/** Claim eligibility survives a lane moving an endpoint into Swagger. */
const DSX_TEMPLATES = new Set(Object.keys(prototypePaths.owners));

const OLD_BY_ID = Object.fromEntries(before.surfaces.map((s) => [s.id, s]));

/** Whether base surface `oldId` showed at the P0 base (base claims only). */
export function shownBefore(oldId, state) {
  const s = OLD_BY_ID[oldId];
  if (!state.launch) return true;
  if (s.parent && !shownBefore(s.parent, state)) return false;
  if (s.experiments && !state.experimentsBasic) return false;
  if (s.ships === "shown") return true;
  if (s.ships === "experimentsBasic" && state.experimentsBasic) return true;
  return LEGACY_CLAIMS.has(oldId);
}

// Current id -> the base surfaces it came from (P0 splits keep their base's
// visibility; an id with no base is a P0 addition, which shipped hidden).
const BASE_OF = {};
for (const old of before.surfaces)
  for (const id of splits[old.id] ?? [old.id])
    (BASE_OF[id] ??= []).push(old.id);

/** The P0 baseline of a current surface id. */
export function baselineShown(id, state) {
  if (!state.launch) return true;
  const olds = BASE_OF[id];
  return Boolean(olds) && olds.every((old) => shownBefore(old, state));
}

/** The ids the WORKS slots claim. */
export const liveClaims = (slots) =>
  new Set(NEW_SLOTS.flatMap((slot) => slots[slot] ?? []));

/**
 * Whether a surface shows under the contract: its baseline, unless a live
 * claim applies to it (parent shown, experimentsBasic for an experiments
 * surface). A shared host follows its parts and ignores a claim on itself.
 */
// Independent expected contract; never import the implementation hold set.
export const HELD_749 = new Set([
  "list.organize.folders",
  "dataset.settings",
  "exp.new.scope",
  "exp.runControl",
  "evals.dag",
]);

// DSX J09 feature-age ruling (release-lead 767/769/770): only these
// pre-existing import surfaces override the historical hidden baseline.
// HF visibility is not enablement; nohide-import.test.jsx pins its disable.
export const NOHIDE_IMPORT_SURFACES = new Set([
  "hf.create",
  "hf.addToExisting",
  "dataset.imports.blankGrid",
  "dataset.addRows",
]);

// All-on release flip (owner Q2; RL 1380/1381), experimentsBasic family: these
// ship shown behind the experimentsBasic flag, so they follow the flag rather
// than their P0 baseline or a claim.
export const ALL_ON_EXPERIMENTS_BASIC = new Set([
  "exp.new",
  "dataset.tab.experiments",
  "dataset.experimentDefaults",
]);

export function expectedShown(id, state, claims) {
  // J09 REVISED / 767: the named classic-only split is not a WORKS activation.
  // Census A/B moves only the enumerated pre-existing options to this split;
  // every NEW surface keeps its contract.
  if (id === "exp.classic") return true;
  if (!state.launch || NOHIDE_IMPORT_SURFACES.has(id)) return true;
  if (HELD_749.has(id)) return false;
  const s = SURFACE_BY_ID[id];
  if (s.anyOf)
    return s.anyOf.some((part) => expectedShown(part, state, claims));
  if (ALL_ON_EXPERIMENTS_BASIC.has(id)) return state.experimentsBasic;
  if (!claims.has(id)) return baselineShown(id, state);
  if (s.parent && !expectedShown(s.parent, state, claims)) return false;
  if (s.experiments && !state.experimentsBasic) return false;
  return true;
}

const moved = (id, state, claims) =>
  expectedShown(id, state, claims) !== baselineShown(id, state);

/** Every surface whose visibility differs from the contract. */
export function surfaceDrift(shownIn, state, claims) {
  return LAUNCH_SURFACES.flatMap(({ id }) => {
    const expected = expectedShown(id, state, claims);
    const actual = shownIn(id, state);
    return actual === expected ? [] : [{ id, expected, actual }];
  });
}

/**
 * Every inventory option whose hidden state differs from the contract. An
 * option no claim moves keeps its baseline; one a claim moves is hidden
 * while any of its surfaces stays hidden (a claim never hides an option).
 */
export function optionDrift(shownIn, state, claims) {
  const hiddenAtBase = new Set(
    !state.launch
      ? []
      : state.experimentsBasic
        ? optionsBefore.hidden.launchBasic
        : optionsBefore.hidden.launch,
  );
  return Object.keys(inventory.classes).flatMap((option) => {
    const surfaces = surfacesOfOption(option);
    const actual = surfaces.some((s) => !shownIn(s.id, state));
    const expected =
      hiddenAtBase.has(option) &&
      (!surfaces.some((s) => moved(s.id, state, claims)) ||
        surfaces.some((s) => !expectedShown(s.id, state, claims)));
    return actual === expected ? [] : [{ option, expected, actual }];
  });
}

const PAGES = [
  "/dashboard/develop/d1",
  "/dashboard/evaluations/t1",
  "/dashboard/develop",
];
const reOf = ({ regex, flags }) => new RegExp(regex, flags);
const keyHits = (rule, value) =>
  !rule.values || rule.values.includes(String(value));

/**
 * Every base route and deep-link key whose outcome differs from the
 * contract. A rule no moved surface carries keeps its base outcome (a hidden
 * base surface's route lands on its fallback, its key is dropped); a rule a
 * moved surface carries follows the surfaces that carry it.
 */
export function entryDrift(
  { launchRedirect, sanitizeLaunchSearch },
  state,
  claims,
) {
  const drift = [];
  const hiddenNow = (s) => !expectedShown(s.id, state, claims);
  for (const old of before.surfaces) {
    const hidden = !shownBefore(old.id, state);
    for (const route of old.entries?.routes ?? []) {
      const hits = LAUNCH_SURFACES.flatMap((s) =>
        (s.entries?.routes ?? []).flatMap((r) => {
          const match = r.re.exec(route.sample);
          return match ? [{ s, r, match }] : [];
        }),
      );
      let expected = null;
      if (hits.some(({ s }) => moved(s.id, state, claims))) {
        const hit = state.launch && hits.find(({ s }) => hiddenNow(s));
        if (hit) expected = hit.r.fallback.replace("$1", hit.match[1] ?? "");
      } else if (hidden) {
        const match = reOf(route.re).exec(route.sample);
        expected = route.fallback.replace("$1", match[1] ?? "");
      }
      const actual =
        launchRedirect({ pathname: route.sample }, state)?.to ?? null;
      if (actual !== expected)
        drift.push({ route: route.sample, of: old.id, expected, actual });
    }
    for (const rule of old.entries?.search ?? []) {
      const page = PAGES.find((p) => reOf(rule.on).test(p)) ?? "";
      for (const value of rule.values ?? ["1"]) {
        const carriers = LAUNCH_SURFACES.filter((s) =>
          (s.entries?.search ?? []).some(
            (r) =>
              r.on.test(page) &&
              matchesGlob(r.key, rule.key) &&
              keyHits(r, value),
          ),
        );
        const { changed } = sanitizeLaunchSearch(
          `?${rule.key}=${value}`,
          page,
          state,
        );
        // searchKeyOwner: an explicit move follows its new carrier, not the old claim.
        const owner = searchKeyOwners.find(
          (entry) =>
            entry.kind === "searchKeyOwner" &&
            entry.from === old.id &&
            entry.key === rule.key,
        );
        const target = owner && SURFACE_BY_ID[owner.to];
        let ok;
        if (target) {
          const rules = target.entries?.search ?? [];
          const keepsRule = rules.some(
            (r) =>
              r.key === rule.key &&
              r.on.source === rule.on.regex &&
              r.on.flags === rule.on.flags &&
              JSON.stringify(r.values) === JSON.stringify(rule.values) &&
              r.replace === rule.replace,
          );
          ok =
            keepsRule &&
            !carriers.some((s) => s.id === old.id) &&
            changed ===
              (state.launch && (hiddenNow(target) || carriers.some(hiddenNow)));
        } else if (!state.launch) ok = !changed;
        else if (carriers.some((s) => moved(s.id, state, claims)))
          ok = changed === carriers.some(hiddenNow);
        // Some keys are shared with another hidden surface (entry=http).
        else ok = !hidden || changed;
        if (!ok)
          drift.push({
            key: `${rule.key}=${value}`,
            of: old.id,
            page,
            changed,
          });
      }
    }
  }
  return drift;
}

// ---------------------------------------------------------------------------
// Slot validation.

/** [code, comment] of one source line (`//` outside a string literal). */
function splitLine(line) {
  let quote = null;
  for (let i = 0; i < line.length; i += 1) {
    const c = line[i];
    if (quote) {
      if (c === "\\") i += 1;
      else if (c === quote) quote = null;
    } else if (c === '"' || c === "'" || c === "`") quote = c;
    else if (c === "/" && line[i + 1] === "/")
      return [line.slice(0, i), line.slice(i + 2)];
  }
  return [line, ""];
}

const count = (source, text) => source.split(text).length - 1;

/**
 * The string entries of one marked WORKS block, each with the walk id its
 * trailing comment names, else the comment block above it.
 */
export function blockEntries(source, slot, kind) {
  const lines = source.split("\n");
  const begin = lines.findIndex((l) => l.includes(`// BEGIN ${slot} ${kind} `));
  const end = lines.findIndex((l) => l.includes(`// END ${slot} ${kind}`));
  const entries = [];
  let note = "";
  let afterCode = true;
  for (const line of lines.slice(begin + 1, end)) {
    const [code, comment] = splitLine(line);
    if (!code.trim()) {
      if (!comment) continue;
      note = afterCode ? comment : `${note}\n${comment}`;
      afterCode = false;
      continue;
    }
    afterCode = true;
    for (const m of code.matchAll(/"([^"\\]*)"|'([^'\\]*)'/g))
      entries.push({
        value: m[1] ?? m[2],
        walk: (comment.match(WALK_ID) ?? note.match(WALK_ID))?.[0] ?? null,
      });
  }
  return entries;
}

const sameList = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * Everything wrong with the WORKS slots: legacy slots changed, markers
 * missing or repeated, a claim that is unknown, another group's, a legacy
 * claim, a shared host, a Results activation id, claimed twice, or without
 * a two-tenant walk id that e2e/FLOWS.md lists; a path that no WORKS claim
 * in its slot backs, that is no DSX prototype template, or without a walk;
 * and a live claim set (`live`, as the switch reads it) that is not the
 * union of the slots. A path's backing surfaces are its pinned P0 owners
 * with any pathOwner entry applied (`pathOwners`, registryPathOwners.js).
 */
export function claimViolations({
  MADE_REAL,
  MADE_REAL_PATHS,
  source,
  flows,
  // Omitting the optional live-set check already yields undefined.
  live = undefined,
  pathOwners = resolvePathOwners(),
}) {
  const out = [];
  const add = (code, slot, value, detail) =>
    out.push({
      code,
      slot,
      ...(value === undefined ? {} : { value }),
      ...(detail === undefined ? {} : { detail }),
    });
  const registries = { claims: MADE_REAL, paths: MADE_REAL_PATHS };
  const legacy = { claims: before.claims, paths: before.paths };
  const legacySource = { claims: before.claimSource, paths: before.pathSource };
  const names = { claims: "MADE_REAL", paths: "MADE_REAL_PATHS" };
  for (const kind of ["claims", "paths"]) {
    const head = `export const ${names[kind]} = Object.freeze({`;
    if (!source.includes(`${head}${legacySource[kind]}`))
      add("legacy-bytes", kind);
    for (const [slot, values] of Object.entries(legacy[kind]))
      if (!sameList(registries[kind][slot], values))
        add("legacy-values", slot, kind);
    if (
      !sameList(Object.keys(registries[kind]), [...LEGACY_SLOTS, ...NEW_SLOTS])
    )
      add("slot-keys", kind, Object.keys(registries[kind]));
  }
  const walks = new Set(
    [...flows.matchAll(/^### ([A-Z][A-Z0-9]*-E2E-\d{3})\b/gm)].map((m) => m[1]),
  );
  const checkWalk = (slot, value, walk) => {
    if (!walk) add("missing-walk", slot, value);
    else if (!walks.has(walk)) add("unknown-walk", slot, value, walk);
  };
  const claimedIn = new Map();
  const pathIn = new Map();
  for (const slot of NEW_SLOTS) {
    const entries = {};
    for (const kind of ["claims", "paths"]) {
      const marks = [`// BEGIN ${slot} ${kind} `, `// END ${slot} ${kind}`];
      const ok =
        marks.every((m) => count(source, m) === 1) &&
        source.indexOf(marks[0]) < source.indexOf(marks[1]);
      if (!ok) add("markers", slot, kind);
      entries[kind] = ok ? blockEntries(source, slot, kind) : [];
      const values = registries[kind][slot] ?? [];
      if (
        ok &&
        !sameList(
          entries[kind].map((e) => e.value),
          values,
        )
      )
        add("outside-markers", slot, kind);
    }
    const walkOf = (kind, value) =>
      entries[kind].find((e) => e.value === value)?.walk ?? null;
    const claims = MADE_REAL[slot] ?? [];
    for (const id of claims) {
      const surface = SURFACE_BY_ID[id];
      if (!surface) add("unknown-id", slot, id);
      else {
        if (surface.madeRealBy !== slot)
          add("wrong-slot", slot, id, surface.madeRealBy);
        if (surface.anyOf) add("shared-host", slot, id);
      }
      if (LEGACY_CLAIMS.has(id)) add("legacy-id", slot, id);
      if (id.startsWith(RESULTS_PREFIX)) add("results-id", slot, id);
      if (claimedIn.has(id)) add("duplicate", slot, id, claimedIn.get(id));
      else claimedIn.set(id, slot);
      checkWalk(slot, id, walkOf("claims", id));
    }
    const paths = MADE_REAL_PATHS[slot] ?? [];
    if (paths.length && !claims.length) add("path-without-claim", slot);
    for (const path of paths) {
      if (!DSX_TEMPLATES.has(path)) add("unknown-path", slot, path);
      else if (claims.length) {
        const owners = pathOwners[path];
        if (!owners.some((id) => SURFACE_BY_ID[id]?.madeRealBy === slot))
          add("wrong-path-slot", slot, path);
        if (!owners.some((id) => claims.includes(id)))
          add("path-without-surface", slot, path);
      }
      if (pathIn.has(path)) add("duplicate-path", slot, path, pathIn.get(path));
      else pathIn.set(path, slot);
      checkWalk(slot, path, walkOf("paths", path));
    }
  }
  if (live) {
    const union = new Set(Object.values(MADE_REAL).flat());
    const extra = [...live].filter((id) => !union.has(id)).sort();
    const missing = [...union].filter((id) => !live.has(id)).sort();
    if (extra.length || missing.length)
      add("live-set", "claims", extra, missing);
  }
  return out;
}
