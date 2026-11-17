// Test-only path ownership declarations (kind "pathOwner" in
// __fixtures__/registry-wiring-additions-705.json). A path of the pinned P0
// prototype set (__fixtures__/registry-prototype-paths-705.json) can move
// once from its P0 backing surface to a claim-gated surface of the same WORKS
// slot, a declared lane addition included. registryClaims.js reads the
// resolved owners: the moved path is then backed by its new owner alone.
// The pinned fixture itself never changes, and the set of claimable paths
// stays the P0 set.
import prototypePaths from "../__fixtures__/registry-prototype-paths-705.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import wiringAdditions from "../__fixtures__/registry-wiring-additions-705.json";
import { LAUNCH_SURFACES } from "../launchSurfaces";
import { ownerTarget, text } from "./registryOwnerTargets";

/** The pinned P0 backing surfaces of each prototype path. */
export const P0_PATH_OWNERS = Object.freeze(
  Object.fromEntries(
    Object.entries(prototypePaths.owners).map(([path, ids]) => [
      path,
      Object.freeze([...ids]),
    ]),
  ),
);
const FIELDS = new Set(["kind", "path", "from", "to", "lane", "feature"]);
const fail = (code, path) => {
  throw new Error(`Path owner ${code}: ${path ?? "<unknown>"}`);
};

/**
 * Every pinned path with its backing surfaces, each pathOwner entry applied:
 * `from` (one of the path's P0 owners) is replaced by `to`. The target takes
 * the capOwner rule, except that an `experiments` surface is admitted (see
 * registryOwnerTargets.js), and must sit in `from`'s slot. One active entry
 * per path. Other entry kinds are left to their own resolvers.
 */
export function resolvePathOwners(
  entries = wiringAdditions,
  { laneDeclarations = laneAdditions, surfaces = LAUNCH_SURFACES } = {},
) {
  if (!Array.isArray(entries)) fail("not-list");
  const owners = Object.fromEntries(
    Object.entries(P0_PATH_OWNERS).map(([path, ids]) => [path, [...ids]]),
  );
  const seen = new Set();
  for (const entry of entries) {
    if (entry?.kind !== "pathOwner") continue;
    const { path, from, to, lane, feature } = entry;
    for (const key of Object.keys(entry))
      if (!FIELDS.has(key)) fail("unknown-field", path);
    if (!text(path) || !Object.hasOwn(P0_PATH_OWNERS, path))
      fail("unknown-path", path);
    if (!text(from) || !P0_PATH_OWNERS[path].includes(from))
      fail("wrong-from", path);
    if (!text(lane)) fail("invalid-lane", path);
    if (!text(feature)) fail("invalid-feature", path);
    const target = ownerTarget(to, {
      laneDeclarations,
      surfaces,
      experimentsOk: true,
    });
    if (target.code) fail(target.code, path);
    const fromSlot = surfaces.findLast((item) => item.id === from)?.madeRealBy;
    if (target.slot !== fromSlot) fail("cross-slot", path);
    if (seen.has(path)) fail("duplicate-path", path);
    seen.add(path);
    owners[path] = [
      ...new Set(owners[path].map((id) => (id === from ? to : id))),
    ];
  }
  return owners;
}
