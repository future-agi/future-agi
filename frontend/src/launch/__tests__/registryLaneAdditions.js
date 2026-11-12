// Lane additions are append-only declarations, not claims. They ship hidden;
// only madeReal.js's own-slot + FLOWS.md walk contract may reveal them.
// P0 option ownership is pinned separately because the older visibility
// fixtures do not record options. Never derive provenance from the live tree.
import before from "../__fixtures__/registry-before-705.json";
import p0 from "../__fixtures__/registry-p0-options-705.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import inventory from "../__fixtures__/inventory-classes.json";
import {
  SURFACE_BY_ID,
  matchesGlob,
  surfacesOfOption,
} from "../launchSurfaces";
import { NEW_SLOTS, RESULTS_PREFIX } from "./registryClaims";

export { laneAdditions };

export const P0_ADDITIONS = Object.freeze([
  "dataset.imports.columnRoles",
  "dataset.imports.transforms",
  "exp.results.evalVersionImpact",
  "exp.results.bestOf",
  "exp.results.addToDataset",
  "list.organize.metadata",
  "list.organize.agents",
  "dataset.evalScoring",
  "dataset.export",
  "exp.rename",
  "dataset.rowTab.activity",
]);

const EXISTING = new Set([
  ...before.surfaces.map((s) => s.id),
  ...Object.keys(p0.surfaces),
  ...P0_ADDITIONS,
]);
const FIELDS = new Set([
  "id",
  "slot",
  "ships",
  "parent",
  "experiments",
  "splitFrom",
  "options",
  "walk",
]);
const text = (value) => typeof value === "string" && value.trim().length > 0;
const owns = (patterns, option) =>
  patterns.some((pattern) => matchesGlob(pattern, option));
const sorted = (ids) => [...ids].sort();
const same = (a, b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));

/** Validate the declaration, live metadata, and every concrete option move. */
export function laneAdditionViolations(entries = laneAdditions) {
  if (!Array.isArray(entries)) return [{ code: "not-list" }];
  const out = [];
  const add = (code, id, detail) =>
    out.push({ code, id, ...(detail === undefined ? {} : { detail }) });
  const seen = new Set();
  const moves = new Map();
  for (const entry of entries) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      add("not-entry", undefined);
      continue;
    }
    const {
      id,
      slot,
      ships,
      parent,
      experiments,
      splitFrom,
      options = [],
    } = entry;
    for (const key of Object.keys(entry))
      if (!FIELDS.has(key)) add("unknown-field", id, key);
    if (!text(id)) add("invalid-id", id);
    if (EXISTING.has(id)) add("existing-id", id);
    if (seen.has(id)) add("duplicate-id", id);
    seen.add(id);
    if (text(id) && id.startsWith(RESULTS_PREFIX)) add("results-id", id);
    if (!NEW_SLOTS.includes(slot) || slot === "RES")
      add("invalid-slot", id, slot);
    if (ships !== "hidden") add("not-hidden", id);
    if (entry.walk !== undefined && entry.walk !== null)
      add("declaration-walk", id);
    if (experiments !== undefined && typeof experiments !== "boolean")
      add("invalid-experiments", id);
    if (parent !== undefined && (!text(parent) || !SURFACE_BY_ID[parent]))
      add("unknown-parent", id, parent);
    if (splitFrom !== undefined && !Object.hasOwn(p0.surfaces, splitFrom))
      add("unknown-source", id, splitFrom);
    if (!Array.isArray(options) || options.some((o) => !text(o))) {
      add("invalid-options", id);
      continue;
    }
    if (new Set(options).size !== options.length) add("duplicate-option", id);
    if (options.length && !splitFrom) add("missing-source", id);

    const surface = SURFACE_BY_ID[id];
    if (!surface) add("missing-surface", id);
    else {
      if (surface.ships !== "hidden") add("surface-not-hidden", id);
      if (surface.madeRealBy !== slot) add("surface-slot", id);
      if (surface.parent !== parent) add("surface-parent", id);
      if (Boolean(surface.experiments) !== Boolean(experiments))
        add("surface-experiments", id);
      if (!same(surface.options, options)) add("surface-options", id);
      // The same parent/child graph rules apply to new ids; reject cycles
      // before the visibility contract recursively visits them.
      const ancestors = new Set([id]);
      let next = surface.parent;
      while (next) {
        if (ancestors.has(next)) {
          add("parent-cycle", id);
          break;
        }
        ancestors.add(next);
        if (!SURFACE_BY_ID[next]) break;
        next = SURFACE_BY_ID[next].parent;
      }
    }

    for (const pattern of options) {
      const matches = Object.keys(inventory.classes).filter((o) =>
        matchesGlob(pattern, o),
      );
      if (!matches.length) add("unknown-option", id, pattern);
      for (const option of matches) {
        if (!owns(p0.surfaces[splitFrom] ?? [], option)) {
          add("option-not-on-source", id, option);
          continue;
        }
        const sources = moves.get(option) ?? new Map();
        if (sources.has(splitFrom) && sources.get(splitFrom) !== id)
          add("duplicate-move", id, option);
        sources.set(splitFrom, id);
        moves.set(option, sources);
      }
    }
  }
  for (const [option, sources] of moves) {
    const expected = new Set(
      Object.entries(p0.surfaces)
        .filter(([, patterns]) => owns(patterns, option))
        .map(([id]) => sources.get(id) ?? id),
    );
    const actual = surfacesOfOption(option).map((s) => s.id);
    if (!same(actual, expected))
      add("option-owners", option, {
        expected: sorted(expected),
        actual: sorted(actual),
      });
  }
  return out;
}
