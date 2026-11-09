// One list of ways to fill a dataset (DL#3, DL#4): the Add dataset drawer
// shows all of them; the Add rows drawer drops the dataset-only ones and
// adds `From traces`. Same order, icons and copy in both.
// options-truth L5 G5 (the interface contract): a door is listed only when it previews what it
// creates and ends on a dataset. the interface contract: Hugging Face (repo@revision, the
// licence in the v1 note) and the interface contract-8a Generate synthetic rows are listed.
// Pushing rows with the SDK stays out
// until the SDK ships, like every SDK-only path.
import { isPrototypeMode } from "src/utils/prototype";
import { isLaunchBuild, isSurfaceShown } from "src/launch/launchSwitch";
import {
  PROPOSED_ADD_ROWS_COPY,
  PROPOSED_HF_IMPORT_COPY,
} from "./importAvailabilityCopy";

const DOCS = "https://docs.futureagi.com/docs/dataset";

export const IMPORT_OPTIONS = Object.freeze([
  {
    id: "uploadFile",
    title: "Upload a file",
    subTitle: "CSV, JSON or JSONL, up to 25 MB",
    icons: "upload_file",
    docs: `${DOCS}/features/upload-file`,
    docsLabel: "CSV format",
  },
  {
    id: "fromProduction",
    title: "From production traces",
    subTitle: "Import selected Observe traces, spans or session traces as rows",
    iconify: "mdi:transit-connection-variant",
    docs: `${DOCS}/features/from-traces`,
  },
  {
    id: "manuallyCreateDataset",
    title: "Start with a blank grid",
    subTitle: "Name it, pick the columns, type rows yourself",
    icons: "manual_dataset",
    datasetOnly: true,
  },
  {
    id: "addFromExistingDataset",
    title: "Copy from another dataset",
    subTitle: "Copy rows from another dataset, field by field",
    icons: "add_existing_model",
  },
  {
    id: "taskSuite",
    title: "Task suite",
    subTitle: "Agent tasks with a start state and end-state checks",
    iconify: "mdi:clipboard-check-multiple-outline",
    datasetOnly: true,
  },
  {
    id: "importFromHuggingface",
    title: "Hugging Face",
    // L5 (hf:drawer): revisions and curated-first exist only in prototype.
    subTitle: isPrototypeMode()
      ? "A public dataset at a revision; benchmarks first"
      : "A public dataset from the Hugging Face Hub",
    iconify: "mdi:emoticon-happy-outline",
  },
  // the interface contract-8a: describe the dataset and its columns; the generator writes it.
  {
    id: "createSyntheticData",
    title: "Generate synthetic rows",
    subTitle: "Describe the rows and their columns; a model writes them",
    iconify: "mdi:creation-outline",
    datasetOnly: true,
  },
]);

/** The Add rows drawer's list: no dataset-only paths, plus a blank row. */
export const ROW_IMPORT_OPTIONS = Object.freeze([
  {
    id: "empty-row",
    title: "Add a blank row",
    subTitle: "Opens in the Row view; type the values there",
    icons: "empty_rows",
  },
  ...IMPORT_OPTIONS.filter((o) => !o.datasetOnly),
]);

export const DATASETS_DOCS = DOCS;

// DSX launch integration (the capability contract; inventory create:drawer,
// create:blank-grid, create:task-suite, create:from-production): a launch
// build's New dataset drawer offers only the doors dev serves. Task suite is
// prototype-only (simulated environment), From production traces posts to
// the imports endpoint dev does not have (dataset.fromProduction).
// DSX J09 / 767/769/770 restores the legacy blank-grid and HF doors.
// Manual creation now persists named/typed columns. HF remains visible
// but disabled pending GE-2 acceptance; its worker may lack the `ml` extra.
const LAUNCH_HIDDEN_CREATE = Object.freeze({
  taskSuite: () => isLaunchBuild(),
  fromProduction: () => !isSurfaceShown("dataset.fromProduction"),
  manuallyCreateDataset: () => !isSurfaceShown("dataset.imports.blankGrid"),
  importFromHuggingface: () => !isSurfaceShown("hf.create"),
});

/** Whether the New dataset drawer offers door `id` in this build. */
export const createDoorShown = (id) => !(LAUNCH_HIDDEN_CREATE[id]?.() ?? false);

/** GE-2: one enablement decision for the chooser, row sheet and HF route. */
export const huggingFaceImportDisabled = () => isLaunchBuild();

/** The New dataset drawer's doors in this build (render time). */
export const offeredImportOptions = () =>
  IMPORT_OPTIONS.filter((o) => createDoorShown(o.id)).map((option) =>
    huggingFaceImportDisabled() && option.id === "importFromHuggingface"
      ? {
          ...option,
          disabled: true,
          disabledReason: PROPOSED_HF_IMPORT_COPY.notice,
        }
      : option,
  );

/** Legacy file/empty-row doors use classic writes; GE-2 still disables HF. */
export function rowImportOption(option) {
  if (option.id === "importFromHuggingface" && huggingFaceImportDisabled()) {
    return {
      ...option,
      subTitle: PROPOSED_HF_IMPORT_COPY.addSubtitle,
      disabled: true,
      disabledReason: PROPOSED_HF_IMPORT_COPY.notice,
    };
  }
  if (!isLaunchBuild()) return option;
  // The classic dialog adds a chosen count immediately, not a pending Row view.
  if (option.id === "empty-row") {
    return {
      ...option,
      subTitle: PROPOSED_ADD_ROWS_COPY.emptyRowSubtitle,
    };
  }
  return option;
}

const ROW_OPTION = Object.fromEntries(ROW_IMPORT_OPTIONS.map((o) => [o.id, o]));

/**
 * IT-R1 G5 P3: the dataset page's `Add rows ▾` sheet, most-used first and
 * the blank row last. (`From sessions` is the Sessions tab of the same
 * production sheet, so it is not a second card.)
 */
export const ADD_ROWS_OPTIONS = Object.freeze([
  {
    ...ROW_OPTION.fromProduction,
    subTitle:
      "Observe rows from the last 7 days; starts with failing evaluations",
  },
  ROW_OPTION.uploadFile,
  {
    id: "paste",
    title: "Paste",
    subTitle: "CSV, TSV or JSON lines from a spreadsheet or a log",
    iconify: "mdi:content-paste",
  },
  ROW_OPTION.addFromExistingDataset,
  {
    ...ROW_OPTION.importFromHuggingface,
    // L5 (hf:drawer): the real add writes the rows straight into the dataset.
    subTitle: isPrototypeMode()
      ? "Rows of a public dataset at a revision, as pending changes"
      : "Rows of a public dataset, added to this dataset now",
  },
  // the interface contract-8a: a model drafts rows like the dataset's; they are reviewed first.
  {
    id: "generateRows",
    title: "Generate rows",
    subTitle:
      "A model drafts rows like yours; you review them before they're added",
    iconify: "mdi:creation-outline",
  },
  ROW_OPTION["empty-row"],
]);
