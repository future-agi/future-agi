// Test-only setting ownership declarations (kind "settingOwner" in
// __fixtures__/registry-wiring-additions-705.json). A wizard setting
// (WIZARD_SETTING_SURFACE) or an Add rows tile (ADD_ROWS_TILE_SURFACE) can
// move once from its immutable P0 owner to a claim-gated surface, under the
// capOwner target rule. This changes the assertion, never a launch claim:
// the lane still edits the live map and claims the new surface itself.
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import wiringAdditions from "../__fixtures__/registry-wiring-additions-705.json";
import { LAUNCH_SURFACES } from "../launchSurfaces";
import { ownerTarget, text } from "./registryOwnerTargets";

/** The P0 bytes of both maps (wizardSettings.js, launchSurfaces.js). */
export const P0_SETTING_OWNERS = Object.freeze({
  WIZARD_SETTING_SURFACE: Object.freeze({
    description: "exp.rename",
    moreSettings: "exp.new.record",
    rowLimits: "exp.new.scope",
    sample: "exp.new.scope",
    theseRows: "exp.new.scope",
    version: "dataset.versions",
    splits: "dataset.splits",
  }),
  ADD_ROWS_TILE_SURFACE: Object.freeze({
    fromProduction: "dataset.fromProduction",
    uploadFile: "dataset.addRows",
    paste: "dataset.addRows",
    addFromExistingDataset: "dataset.addRows",
    importFromHuggingface: "hf.addToExisting",
    generateRows: "dataset.imports.generateRowsTile",
    "empty-row": "dataset.addRows",
  }),
});
const FIELDS = new Set(["kind", "map", "key", "from", "to", "lane", "feature"]);
const nameOf = (map, key) => `${map ?? "<unknown>"}.${key ?? "<unknown>"}`;
const fail = (code, name) => {
  throw new Error(`Setting owner ${code}: ${name}`);
};

/**
 * Both maps with every settingOwner entry applied: one active entry per
 * (map, key). Other entry kinds are left to their own resolvers.
 */
export function resolveSettingOwners(
  entries = wiringAdditions,
  { laneDeclarations = laneAdditions, surfaces = LAUNCH_SURFACES } = {},
) {
  if (!Array.isArray(entries)) fail("not-list", nameOf());
  const owners = Object.fromEntries(
    Object.entries(P0_SETTING_OWNERS).map(([map, keys]) => [map, { ...keys }]),
  );
  const seen = new Set();
  for (const entry of entries) {
    if (entry?.kind !== "settingOwner") continue;
    const { map, key, from, to, lane, feature } = entry;
    const name = nameOf(map, key);
    for (const field of Object.keys(entry))
      if (!FIELDS.has(field)) fail("unknown-field", name);
    if (!text(map) || !Object.hasOwn(P0_SETTING_OWNERS, map))
      fail("unknown-map", name);
    if (!text(key) || !Object.hasOwn(P0_SETTING_OWNERS[map], key))
      fail("unknown-key", name);
    if (from !== P0_SETTING_OWNERS[map][key]) fail("wrong-from", name);
    if (!text(lane)) fail("invalid-lane", name);
    if (!text(feature)) fail("invalid-feature", name);
    const target = ownerTarget(to, { laneDeclarations, surfaces });
    if (target.code) fail(target.code, name);
    if (seen.has(name)) fail("duplicate-setting", name);
    seen.add(name);
    owners[map][key] = to;
  }
  return owners;
}

/** The settings whose resolved owner is not their P0 owner. Pure. */
export const settingMoves = (owners) =>
  Object.entries(owners).flatMap(([map, keys]) =>
    Object.entries(keys)
      .filter(([key, to]) => to !== P0_SETTING_OWNERS[map][key])
      .map(([key, to]) => ({ map, key, to })),
  );

/**
 * Every moved setting that does not show literally by its new owner's claim:
 * hidden with no claim, shown by that claim alone and hidden under every
 * other single claim in `ids`, with experimentsBasic OFF and ON.
 * `shownUnder(map, key, state)` reads the live consumer (no held value).
 */
export function settingClaimDrift(moves, ids, shownUnder) {
  const drift = [];
  for (const experimentsBasic of [false, true])
    for (const claim of [null, ...ids]) {
      const state = {
        launch: true,
        experimentsBasic,
        madeReal: new Set(claim ? [claim] : []),
      };
      for (const { map, key, to } of moves) {
        const actual = shownUnder(map, key, state);
        if (actual !== (claim === to))
          drift.push({ map, key, claim, experimentsBasic, actual });
      }
    }
  return drift;
}
