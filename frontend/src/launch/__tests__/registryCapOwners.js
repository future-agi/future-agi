// Test-only ownership declarations. A capability can move once from its
// immutable P0 owner; this changes the assertion, never a launch claim.
// The new owner must be claim-gated: it shows only while its own WORKS slot
// claims it, so a move can never show a capability without that claim.
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import wiringAdditions from "../__fixtures__/registry-wiring-additions-705.json";
import { LAUNCH_SURFACES } from "../launchSurfaces";
import { ownerTarget, text } from "./registryOwnerTargets";

export const P0_CAP_OWNERS = Object.freeze({
  listFolders: "list.organize.folders",
  listStars: "list.organize.star",
  listFilters: "list.organize.query",
  listGroupBy: "list.organize.query",
  listSavedViews: "list.organize.query",
  listRunningPoll: "list.organize.query",
});
const FIELDS = new Set(["kind", "cap", "from", "to", "lane", "feature"]);
const fail = (code, cap) => {
  throw new Error(`Cap owner ${code}: ${cap ?? "<unknown>"}`);
};

/** Wire-site entries remain the responsibility of resolveWiringSites. */
export function resolveCapOwners(
  entries = wiringAdditions,
  { laneDeclarations = laneAdditions, surfaces = LAUNCH_SURFACES } = {},
) {
  if (!Array.isArray(entries)) fail("not-list");
  const owners = { ...P0_CAP_OWNERS };
  const seen = new Set();
  for (const entry of entries) {
    if (entry?.kind !== "capOwner") continue;
    const { cap, from, to, lane, feature } = entry;
    for (const key of Object.keys(entry))
      if (!FIELDS.has(key)) fail("unknown-field", cap);
    if (!text(cap) || !Object.hasOwn(P0_CAP_OWNERS, cap))
      fail("unknown-cap", cap);
    if (from !== P0_CAP_OWNERS[cap]) fail("wrong-from", cap);
    if (!text(lane)) fail("invalid-lane", cap);
    if (!text(feature)) fail("invalid-feature", cap);
    const target = ownerTarget(to, { laneDeclarations, surfaces });
    if (target.code) fail(target.code, cap);
    if (seen.has(cap)) fail("duplicate-cap", cap);
    seen.add(cap);
    owners[cap] = to;
  }
  return owners;
}
