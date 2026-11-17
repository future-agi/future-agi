// Test-only rule for the surface a moved owner may point at. A capability
// (capOwner), a wizard setting or import tile (settingOwner) and a pinned
// prototype path (pathOwner) can each move once from its immutable P0 owner
// (__fixtures__/registry-wiring-additions-705.json). The new owner must be
// claim-gated: only a claim in its own WORKS slot can reveal it.
import p0 from "../__fixtures__/registry-p0-options-705.json";
import splits from "../__fixtures__/registry-splits-705.json";
import { NEW_SLOTS } from "./registrySlots";

const RETIRED = new Set(
  Object.entries(splits)
    .filter(([id, children]) => !children.includes(id))
    .map(([id]) => id),
);

export const text = (value) =>
  typeof value === "string" && value.trim().length > 0;

/**
 * Why a surface (registry metadata) or a lane declaration (`slot` in place of
 * madeRealBy) is not claim-gated; null when only its own claim shows it.
 * `experimentsOk` admits an `experiments` surface (hidden until its claim and
 * the flag both hold). Only a path target takes it: a path claim needs its
 * backing claim in the same slot, never the surface's visibility, while a
 * capability or a setting is pinned shown under its owner's claim with the
 * flag OFF too.
 */
function ungated({ ships, anyOf, parent, experiments }, slot, experimentsOk) {
  if (anyOf !== undefined) return "shared-host-target";
  if (ships === "shown") return "shown-target";
  if (ships === "experimentsBasic") return "flag-target";
  if (experiments && !experimentsOk) return "flag-target";
  if (ships !== "hidden") return "not-hidden-target";
  if (parent !== undefined) return "parent-target";
  if (!NEW_SLOTS.includes(slot)) return "slot-target";
  return null;
}

/**
 * Validate `to` as a new owner. Returns `{code}` for the first rule it
 * breaks, else `{slot}` (the WORKS slot whose claim gates it). A lane
 * addition must be declared, in the registry, and claim-gated by both its
 * declaration and its registry metadata, in the slot the registry gives it.
 */
export function ownerTarget(
  to,
  { laneDeclarations, surfaces, experimentsOk = false },
) {
  if (!text(to)) return { code: "invalid-to" };
  if (RETIRED.has(to)) return { code: "retired-id" };
  if (to.startsWith("exp.results.")) return { code: "results-id" };
  const declared = laneDeclarations.find((item) => item?.id === to);
  if (!Object.hasOwn(p0.surfaces, to) && !declared)
    return { code: "unknown-id" };
  // The last registry entry for an id wins, as SURFACE_BY_ID builds it.
  const surface = surfaces.findLast((item) => item.id === to);
  if (!surface) return { code: "missing-surface" };
  const code = ungated(surface, surface.madeRealBy, experimentsOk);
  if (code) return { code };
  if (!Object.hasOwn(p0.surfaces, to)) {
    const declaredCode = ungated(declared, declared.slot, experimentsOk);
    if (declaredCode) return { code: declaredCode };
    if (declared.slot !== surface.madeRealBy) return { code: "slot-target" };
  }
  return { slot: surface.madeRealBy };
}
