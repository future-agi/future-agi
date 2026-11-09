// Test-only surface injection. The real registry and append-only fixture are
// never changed by a case; launchSwitch reads the mocked module as in a build.
// A case is measured against the P0 baseline alone: the additions a lane
// really ships are dropped before the case's own entries apply, so a real
// split and a case's split of the same option never share it.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import p0 from "../__fixtures__/registry-p0-options-705.json";
import before from "../__fixtures__/registry-before-705.json";
import searchKeyOwners from "../__fixtures__/registry-search-key-owners-705.json";
import inventory from "../__fixtures__/inventory-classes.json";

const isEntry = (entry) =>
  Boolean(entry) && typeof entry === "object" && !Array.isArray(entry);

// Read from disk, not imported: a case mocks the fixture module with its own
// entries, and that mock must never stand in for the tree's real additions.
// A malformed fixture fails registry-705.test.js; here it drops nothing.
const declared = JSON.parse(
  readFileSync(
    resolve(
      process.cwd(),
      "src/launch/__fixtures__/registry-lane-additions-705.json",
    ),
    "utf8",
  ),
);
const REAL_ADDITIONS = Object.freeze(
  Array.isArray(declared) ? declared.filter(isEntry) : [],
);

export const RUN_CONTROL = Object.freeze({
  id: "exp.runControl",
  slot: "XP",
  ships: "hidden",
  experiments: true,
  splitFrom: "exp.results.matrix",
  options: ["action:resume:*", "action:stop-resume"],
  walk: null,
});

/**
 * `actual` with `entries` injected as hidden lane additions. First every
 * addition the module already carries is dropped: this tree's real ones and
 * `carried` (a case's stand-in for a lane's real additions). Their ids leave
 * every shared host, and each split source of a dropped or injected addition
 * gets its P0 options back.
 */
export function moduleWithLaneAdditions(
  actual,
  entries,
  edit = () => {},
  carried = [],
) {
  const ids = new Set(entries.map((entry) => entry.id));
  const real = [...REAL_ADDITIONS, ...carried.filter(isEntry)];
  const dropped = new Set(real.map((entry) => entry.id));
  const sources = new Set(
    [...real, ...entries].map((entry) => entry.splitFrom),
  );
  const surfaces = actual.LAUNCH_SURFACES.filter(
    (s) => !ids.has(s.id) && !dropped.has(s.id),
  ).map((s) => {
    const baseline = sources.has(s.id) ? p0.surfaces[s.id] : undefined;
    const surface = { ...s, options: [...(baseline ?? s.options)] };
    // Shared hosts start with P0 children only. Even a real child whose id
    // this case reuses must be attached by the case's own `edit` below.
    if (s.anyOf)
      surface.anyOf = s.anyOf.filter((id) => Object.hasOwn(p0.surfaces, id));
    return surface;
  });
  for (const entry of entries) {
    const source = surfaces.find((s) => s.id === entry.splitFrom);
    if (source && Array.isArray(entry.options)) {
      // Expand only patterns intersected by a move. This also models moving
      // one concrete option out of a broad bundle without losing its siblings.
      source.options = source.options.flatMap((pattern) => {
        const matches = Object.keys(inventory.classes).filter((o) =>
          actual.matchesGlob(pattern, o),
        );
        const moved = (option) =>
          entry.options.some((p) => actual.matchesGlob(p, option));
        return matches.some(moved)
          ? matches.filter((o) => !moved(o))
          : [pattern];
      });
    }
    surfaces.push({
      id: entry.id,
      ships: entry.ships,
      madeRealBy: entry.slot,
      ...(entry.parent === undefined ? {} : { parent: entry.parent }),
      ...(entry.experiments === undefined
        ? {}
        : { experiments: entry.experiments }),
      options: Array.isArray(entry.options) ? entry.options : [],
    });
  }
  // searchKeyOwner: restore a dropped owner's key; re-injection carries it on the new owner.
  for (const owner of searchKeyOwners) {
    if (
      owner.kind !== "searchKeyOwner" ||
      (!dropped.has(owner.to) && !ids.has(owner.to))
    )
      continue;
    const source = surfaces.find((s) => s.id === owner.from);
    const target = surfaces.find((s) => s.id === owner.to) ?? source;
    const rule = before.surfaces
      .find((s) => s.id === owner.from)
      ?.entries?.search?.find((r) => r.key === owner.key);
    if (!source || !target || !rule)
      throw new Error("Invalid search-key owner provenance");
    const copy = { ...rule, on: new RegExp(rule.on.regex, rule.on.flags) };
    source.entries = {
      ...source.entries,
      search: (source.entries?.search ?? []).filter((r) => r.key !== owner.key),
    };
    target.entries = {
      ...target.entries,
      search: [
        ...(target.entries?.search ?? []).filter((r) => r.key !== owner.key),
        copy,
      ],
    };
  }
  edit(surfaces);
  return {
    ...actual,
    LAUNCH_SURFACES: surfaces,
    SURFACE_BY_ID: Object.fromEntries(surfaces.map((s) => [s.id, s])),
    surfacesOfOption: (option) =>
      surfaces.filter((s) =>
        s.options.some((pattern) => actual.matchesGlob(pattern, option)),
      ),
  };
}
