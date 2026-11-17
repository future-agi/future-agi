/* global __DSX_CENSUS_B_TEST_BUILD__ */
// What this deployment offers of row comments and row assignments, read from
// the launch registry: a surface is offered when the registry ships it or its
// group has made it real (launchSurfaces.js, madeReal.js). Showing unreleased
// parts (launchSwitch.js) does not change the answer, so an entry that is
// shown can say it is not offered without asking the server first.
// Prototype builds (mock data) and the unit-test profile offer both, as the
// launch switch shows everything there; a test reads the deployment's answer
// with setCollabOfferForTest({ all: false }).
import { SURFACE_BY_ID } from "src/launch/launchSurfaces";
import { madeRealSurfaceIds } from "src/launch/madeReal";

export const ROW_COMMENTS_NOT_OFFERED =
  "Row comments are not part of this release.";
export const ROW_ASSIGNING_NOT_OFFERED =
  "Assigning rows is not part of this release.";

const COMMENTS = "dataset.collab.comments";
const ASSIGNMENTS = "dataset.collab.assignments";
const MADE_REAL_IDS = madeRealSurfaceIds();

// The same test as prototypeModeFromEnv (src/utils/prototype.js).
const PROTOTYPE =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
// vite.config.js compiles this to false in every app build; only the
// unit-test profile (vitest.config.js) sets it.
const UNIT_TEST_PROFILE =
  typeof __DSX_CENSUS_B_TEST_BUILD__ !== "undefined" &&
  __DSX_CENSUS_B_TEST_BUILD__ === true;
const OFFER_ALL = PROTOTYPE || UNIT_TEST_PROFILE;

let override = null;

/**
 * Tests only: `{all, madeReal}` replaces the build's answer until called
 * with null. `all: false` reads the registry as a deployment does.
 */
export function setCollabOfferForTest(next) {
  override = next ? { ...next } : null;
}

/** Whether this deployment offers a registered surface. Throws on unknown ids. */
export function surfaceOffered(id) {
  const s = SURFACE_BY_ID[id];
  if (!s) throw new Error(`Unknown launch surface: ${id}`);
  if (override?.all ?? OFFER_ALL) return true;
  if (s.ships === "shown") return true;
  const madeReal = override?.madeReal
    ? new Set(override.madeReal)
    : MADE_REAL_IDS;
  return madeReal.has(id);
}

export const rowCommentsOffered = () => surfaceOffered(COMMENTS);
export const rowAssigningOffered = () => surfaceOffered(ASSIGNMENTS);

/**
 * An assign door (the cell menu's Assign row to…, the results' Assign to…):
 * the dialog where assigning is offered, otherwise the one sentence, as the
 * page's other info notices. `rows` is a row id or a list of them.
 */
export function assignRowOrSay(rows, { open, say }) {
  if (rowAssigningOffered()) {
    open(rows);
    return;
  }
  say(ROW_ASSIGNING_NOT_OFFERED, { variant: "info" });
}
