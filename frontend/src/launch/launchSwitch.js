/* global __DSX_CENSUS_B_TEST_BUILD__ */
// Launch switch: the single answer to "does this DSX
// surface show in this build?".
//   - Prototype builds (VITE_PROTOTYPE_MODE): everything shows, as before.
//   - Unit tests (MODE "test"): everything shows unless a test turns the
//     launch build on (setLaunchOverrideForTest), so component tests keep
//     covering the full UI.
//   - Every other build (dev deploys, the launch, `vite` dev without the
//     prototype flag) is a LAUNCH build: a surface shows only when the
//     registry ships it (`shown`), when the experimentsBasic flag is ON for
//     an `experimentsBasic` surface, or when its registered capability group claims it.
// experimentsBasic defaults ON (all-on release flip): a build
// with VITE_DSX_EXPERIMENTS_BASIC unset uses EXPERIMENTS_BASIC_DEFAULT; only
// the literal VITE_DSX_EXPERIMENTS_BASIC=false build input turns it off.
import { prototypeModeFromEnv } from "src/utils/prototype";
import {
  ADD_ROWS_TILE_SURFACE,
  LAUNCH_GLOSSARY_KEEP,
  LAUNCH_KEYMAP_SURFACES,
  LAUNCH_SURFACES,
  ROW_TAB_SURFACES,
  SURFACE_BY_ID,
  matchesGlob,
} from "./launchSurfaces";
import { madeRealSurfaceIds } from "./madeReal";
import { GLOSSARY_TERM_SURFACES } from "./glossarySurfaces";

// Unreleased parts are shown: every surface in the launch registry is visible
// and the absent-path guard is not installed, so each part says for itself what
// it cannot do yet. Prototype mode stays off (no mock data).
export const SHOW_UNRELEASED_PARTS = true;

/** The committed experimentsBasic default (all-on: ON unless built false). */
export const EXPERIMENTS_BASIC_DEFAULT = true;

/** A launch build: not the prototype, not a unit test. Pure. */
export const launchBuildFromEnv = (env = {}) =>
  !prototypeModeFromEnv(env) && env.MODE !== "test";

/** experimentsBasic from the build env, else the committed default. Pure. */
export function experimentsBasicFromEnv(env = {}) {
  if (env.VITE_DSX_EXPERIMENTS_BASIC === "true") return true;
  if (env.VITE_DSX_EXPERIMENTS_BASIC === "false") return false;
  return EXPERIMENTS_BASIC_DEFAULT;
}

const ENV_STATE = Object.freeze({
  launch: launchBuildFromEnv(import.meta.env),
  experimentsBasic: experimentsBasicFromEnv(import.meta.env),
});

const MADE_REAL_IDS = madeRealSurfaceIds();

// Source admission alone is not composed walked acceptance.
// This hold layer preserves historical claims; only main may remove an id after
// recording its final-head journey and 10x product acceptance.
// main 11-09: all five holds lifted on c2.6 (walk ids DATA-E2E-004, DATA-E2E-020,
// DATA-E2E-021, EXP-E2E-005, EVAL-E2E-072 accepted by the 749 hold owner).
const LAUNCH_749_HOLDS = new Set([]);

// One opt-in read, available only in the separately compiled unit-test profile.
// vite.config.js replaces this constant with false in EVERY app build, even
// --mode test. Neither runtime state nor an environment value can enable it.
export const censusBTestOptIn = () =>
  typeof __DSX_CENSUS_B_TEST_BUILD__ !== "undefined" &&
  __DSX_CENSUS_B_TEST_BUILD__ &&
  import.meta.env.VITE_DSX_CENSUS_B_TEST === "true";

let override = null;

/**
 * Tests only: `{launch, experimentsBasic, madeReal}` replaces the build's
 * state until called with null.
 */
export function setLaunchOverrideForTest(next) {
  override = next ? { ...next } : null;
}

/** The state the gates read: {launch, experimentsBasic, madeReal:Set}. */
export function launchState() {
  return {
    launch: override?.launch ?? ENV_STATE.launch,
    experimentsBasic: override?.experimentsBasic ?? ENV_STATE.experimentsBasic,
    madeReal: override?.madeReal ? new Set(override.madeReal) : MADE_REAL_IDS,
  };
}

/** Whether this build hides what the launch does not ship. */
export const isLaunchBuild = () => launchState().launch;

/** Whether the experimentsBasic flag is ON (only meaningful in a launch build). */
export const experimentsBasicOn = () => launchState().experimentsBasic;

/** Whether a surface shows; only census holds read the test opt-in. Throws on unknown ids. */
export function surfaceShownIn(id, state) {
  const s = SURFACE_BY_ID[id];
  if (!s) throw new Error(`Unknown launch surface: ${id}`);
  if (override?.showUnreleased ?? SHOW_UNRELEASED_PARTS) return true;
  if (!state.launch) return true;
  // A shared host (anyOf) follows its features; a claim on it is ignored.
  if (s.anyOf) return s.anyOf.some((part) => surfaceShownIn(part, state));
  if (s.parent && !surfaceShownIn(s.parent, state)) return false;
  if (s.experiments && !state.experimentsBasic) return false;
  if (LAUNCH_749_HOLDS.has(id) && !censusBTestOptIn()) return false;
  if (s.ships === "shown") return true;
  if (s.ships === "experimentsBasic")
    return state.experimentsBasic || state.madeReal.has(id);
  return state.madeReal.has(id);
}

/** Whether a surface shows in this build. */
export const isSurfaceShown = (id) => surfaceShownIn(id, launchState());

/** The ids of every surface this build hides. */
export function hiddenSurfaceIds(state = launchState()) {
  return LAUNCH_SURFACES.filter((s) => !surfaceShownIn(s.id, state)).map(
    (s) => s.id,
  );
}

const hiddenWith = (pick, state) =>
  LAUNCH_SURFACES.filter((s) => !surfaceShownIn(s.id, state)).flatMap(
    (s) => pick(s.entries ?? {}) ?? [],
  );

/** Whether a palette verb (command id) shows. */
export function verbShownIn(commandId, state = launchState()) {
  if (!state.launch || !commandId) return true;
  return !hiddenWith((e) => e.verbs, state).some((g) =>
    matchesGlob(g, commandId),
  );
}

/** Whether a key shortcut works / is listed on a keymap surface. */
export function shortcutShownIn(surface, action, state = launchState()) {
  if (!state.launch) return true;
  const keymapSurfaces = state.experimentsBasic
    ? [...LAUNCH_KEYMAP_SURFACES, "newExperiment"]
    : LAUNCH_KEYMAP_SURFACES;
  if (surface && !keymapSurfaces.includes(surface)) return false;
  const key = `${surface}:${action}`;
  return !hiddenWith((e) => e.shortcuts, state).some((g) =>
    matchesGlob(g, key),
  );
}

/**
 * Whether a glossary term shows. A launch build keeps only the grid terms
 * until the glossary is claimed; after that each term shows while a surface
 * of the feature it explains shows (glossarySurfaces.js), and a term with no
 * surface never does.
 */
export function glossaryShownIn(key, state = launchState()) {
  if (!state.launch) return true;
  if (!surfaceShownIn("palette.glossary", state)) {
    return LAUNCH_GLOSSARY_KEEP.includes(key);
  }
  const surfaces = GLOSSARY_TERM_SURFACES[key] ?? [];
  return surfaces.some((id) => surfaceShownIn(id, state));
}

/** Whether a dataset page tab (DevelopBar value) shows. */
export function datasetTabShownIn(tab, state = launchState()) {
  if (!state.launch) return true;
  return !hiddenWith((e) => e.tabs, state).includes(tab);
}

export const isVerbShown = (id) => verbShownIn(id);
export const isShortcutShown = (surface, action) =>
  shortcutShownIn(surface, action);
export const isGlossaryShown = (key) => glossaryShownIn(key);
export const isDatasetTabShown = (tab) => datasetTabShownIn(tab);

/** Split row tabs are independent; ordinary Row/Raw/Annotations stay shown. */
export function rowTabShownIn(tab, state = launchState()) {
  const id = ROW_TAB_SURFACES[tab];
  return !id || surfaceShownIn(id, state);
}

export const isRowTabShown = (tab) => rowTabShownIn(tab);

/** The Add rows sheet tiles this build offers (unknown tile ids: none). */
export function addRowsTilesIn(options, state = launchState()) {
  if (!state.launch) return options;
  return (options ?? []).filter((o) => {
    const id = ADD_ROWS_TILE_SURFACE[o?.id];
    return Boolean(id) && surfaceShownIn(id, state);
  });
}

export const addRowsTiles = (options) => addRowsTilesIn(options);
