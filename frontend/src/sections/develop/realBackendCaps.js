// L1 (the capability contract): what the Datasets list and the dataset grid may
// offer against the REAL backend. A non-prototype build talks to dev's
// Django, which serves the list through `GET develops/get-datasets/` (name
// search, sort, paging; eight fields per row) and writes cells and rows
// straight away (`update_cell_value`, `delete_row`): there is no list query
// endpoint, no folders, stars, saved views, facets or versions. Everything
// those need is hidden here, never sent to a 400.
//
// Prototype mode (the mock world, VITE_PROTOTYPE_MODE) keeps the full DSX
// surface. Unit tests (MODE "test") keep it too, so the existing suites,
// written for the DSX surface, run unchanged; an L1 test switches the real
// set on with setRealBackendForTests(true).
//
// The real set is L6's launch build (launchSwitch.isLaunchBuild: not the
// prototype, not a unit test), so the two switches never disagree.
import {
  isLaunchBuild,
  launchState,
  surfaceShownIn,
} from "src/launch/launchSwitch";
import { productionImportCaps } from "src/sections/common/AddData/productionImportCaps";

let override = null;

/** True when the page talks to the real backend (not the mock world). */
export function isRealBackend() {
  return override ?? isLaunchBuild();
}

/** Tests only: force the real (true) or the prototype (false) set; null resets. */
export function setRealBackendForTests(value) {
  override = value == null ? null : Boolean(value);
}

/**
 * The list columns get-datasets answers (DatasetListItemSerializer on dev:
 * id, name, number_of_datapoints, number_of_experiments,
 * number_of_optimisations, derived_datasets, created_at, dataset_type).
 * Every other DSX list column (Last experiment, Baseline, Best, Updated,
 * Version, Owner, Source, Folder, Agents, Metadata) has no source on dev.
 */
export const REAL_LIST_COLUMNS = Object.freeze([
  "name",
  "numberOfDatapoints",
  "numberOfExperiments",
  "createdAt",
  "numberOfOptimisations",
]);

/** Metadata columns remain absent from both table and picker until qualified. */
export const realListColumnIds = () =>
  datasetCaps(true).listColumns
    ? [...REAL_LIST_COLUMNS, "owner", "source", "updatedAt"]
    : REAL_LIST_COLUMNS;

/** Columns shown by default on the real backend (the rest one click away). */
export const REAL_LIST_DEFAULT_VISIBLE = Object.freeze([
  "name",
  "numberOfDatapoints",
  "createdAt",
]);

/**
 * Sort fields get-datasets orders by (GetDatasetsView column_mapping); any
 * other field is ignored there, which would leave the list unordered.
 */
export const REAL_SORT_FIELDS = Object.freeze([
  "name",
  "number_of_datapoints",
  "number_of_experiments",
  "number_of_optimisations",
  "created_at",
]);

/** Stale URL/layout Updated sorts retain the int6 fallback while unclaimed. */
export const realListSortFields = () =>
  datasetCaps(true).listColumns
    ? [...REAL_SORT_FIELDS, "updated_at"]
    : REAL_SORT_FIELDS;

/** The real list keeps its newest-created default; Updated is an optional sort. */
export const REAL_DEFAULT_SORT = "created_at:desc";

/**
 * The row-menu items that work on the real backend: open, duplicate (dev's
 * `clone-dataset`, sent as dev reads it: no dry run, which dev does not
 * know and would answer with a real copy), copy link / id and delete
 * (`delete_dataset`). Hidden: Open experiments and New experiment
 * (Experiments ships separately). Settings and stars are appended only by
 * their own gates; Organize has independent folder, metadata and agent
 * capabilities. Download requires the independent dataset.export capability;
 * its real adapter reads all current rows.
 */
export const REAL_ROW_ACTIONS = Object.freeze([
  "open",
  "duplicate",
  "download",
  "copyLink",
  "copyId",
  "remove",
]);

/**
 * The capability set of this build. Every flag is a plain boolean; the
 * prototype set has them all on.
 */
export function datasetCaps(real = isRealBackend()) {
  const shown = (id) =>
    !real || surfaceShownIn(id, { ...launchState(), launch: true });
  return Object.freeze({
    real,
    // Datasets list.
    listQuery: real ? "get-datasets" : "w9-query",
    listColumns: shown("dataset.list.columns"),
    listFilters: shown("list.organize.query"),
    listGroupBy: shown("list.organize.query"),
    listFolders: shown("list.organize.folders"),
    listStars: shown("list.organize.star"),
    settings: shown("dataset.settings"),
    duplicateMove: shown("dataset.duplicateMove"),
    // The list's Saved views part shows with the list (never hidden). The
    // real saved-views API only serves "grid" with a dataset UUID, so
    // useSavedViews (r3.js savedViewsServed) sends nothing for the list and
    // the menu says saving is not available in this release.
    listSavedViews: shown("list.organize.query"),
    listOrganize: [
      "list.organize.folders",
      "list.organize.metadata",
      "list.organize.agents",
    ].some(shown),
    listRunningPoll: shown("list.organize.query"),
    listFromTracesCommand: !real,
    // Search covers names only on the real backend (name__icontains).
    searchScope: real ? "name" : "full",
    // Opening a dataset lands on its Data tab; the Experiments tab is
    // L3/L6's switch, so a real build never opens there by itself.
    openOnExperimentsTab: !real,
    // Dataset grid: immediate writes, no pending changes, no versions.
    gridWrites: real ? "classic" : "versioned",
    pendingChanges: !real,
    saveVersion: !real,
    // Selection bar items with no dev backend (splits, status and tags,
    // generate-like-these, new experiment, download of a selection).
    selectionVersionedActions: !real,
    // hook(F33): align the production-import command with its capability.
    ...productionImportCaps(real),
  });
}

/** True when the grid writes straight to the backend (no change-set). */
export const gridWritesClassic = () => datasetCaps().gridWrites === "classic";
