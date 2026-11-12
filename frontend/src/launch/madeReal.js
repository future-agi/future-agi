// Approved backend capabilities, grouped by the registry's integration key.
// A surface is visible only when its registered group enables it. The registry
// tests reject unknown IDs and claims from another group. Keep unsupported
// paths absent until an endpoint and its tenant-isolation checks are available.

/** @type {Readonly<Record<"L1"|"L2"|"L3"|"L4"|"L5"|"L6"|"XP"|"RES"|"IN"|"ORG"|"GRID"|"EVAL"|"COLLAB"|"SRC", readonly string[]>>} */
export const MADE_REAL = Object.freeze({
  L1: Object.freeze([
    // Backend dataset list supports name search, sorting and pagination.
    "list.real",
    // Cell edits, row deletion and row duplication use the classic grid API.
    "dataset.gridWrites",
  ]),
  L2: Object.freeze([]),
  // The classic Experiments tab uses experiments/v2/list/ and still requires
  // experimentsBasic to be enabled.
  L3: Object.freeze(["dataset.tab.experiments"]),
  // Column writes persist role metadata; get-dataset-table returns it.
  // Experiment wizard actions remain gated by experimentsBasic.
  L4: Object.freeze(["dataset.columnRole"]),
  L5: Object.freeze([]),
  // Registry-native surfaces are controlled by their own shown flags.
  L6: Object.freeze([]),
  // BEGIN XP claims (append only; include the two-tenant walk id)
  XP: Object.freeze([
    // T-XP claims go here after acceptance.
    "exp.rerun", // PROMPT-E2E-002: classic prompt/model variants.
    "exp.runControl", // DATA-E2E-021
    "exp.new.scope", // EXP-E2E-005
    "exp.new.splitScope", // EXP-E2E-006
    // 708 add.61a F02: the New experiment kinds
    // menu, Agent graph only. Already running (exp.new.runningSources, SRC)
    // stays unclaimed. Dark while experimentsBasic is OFF (experiments child
    // of exp.new). Agent targets are admitted by the server's GraphVersion
    // authorization check (experiment_target_admission.py).
    "exp.new.kindMenu", // PROMPT-E2E-007
  ]),
  // END XP claims

  // BEGIN RES claims (append only; include the two-tenant walk id)
  RES: Object.freeze([
    // T-RES claims go here after acceptance. P0 reserves, but does not claim:
    // exp.results.matrix, exp.results.paths, exp.results.summary,
    // exp.results.traces, exp.results.iterateDock, exp.results.evalVersionImpact,
    // exp.results.bestOf, exp.results.addToDataset.
    // Main owns all Results activation and child gating (705 addendum 4).
  ]),
  // END RES claims

  // BEGIN IN claims (append only; include the two-tenant walk id)
  IN: Object.freeze([
    // T-IN claims go here after acceptance.
  ]),
  // END IN claims

  // BEGIN ORG claims (append only; include the two-tenant walk id)
  ORG: Object.freeze([
    // T-ORG claims go here after acceptance.
    // DATA-E2E-004: dataset name, description and tags from the list row and header.
    "dataset.settings",
    // DATA-E2E-020: persisted workspace folders, Organize and drag moves.
    "list.organize.folders",
  ]),
  // END ORG claims

  // BEGIN GRID claims (append only; include the two-tenant walk id)
  GRID: Object.freeze([
    // T-GRID claims go here after acceptance.
    "dataset.splits", // DATA-E2E-008
    "dataset.export", // DATA-E2E-005
    "dataset.filtersSystem.pills", // DATA-E2E-048
    "dataset.runPromptEdit", // DATA-E2E-009, DATA-E2E-011
    "dataset.savedViews", // DATA-E2E-049
  ]),
  // END GRID claims

  // BEGIN EVAL claims (append only; include the two-tenant walk id)
  EVAL: Object.freeze([
    // T-EVAL claims go here after acceptance.
    "evals.dag", // EVAL-E2E-072
    // The pass threshold set in a dataset evaluation's editor, saved on that
    // evaluation's run settings without saving a new version.
    "evals.editorPassThreshold", // EVAL-E2E-017
    // The evaluation editor's header naming the saved version configured.
    "evals.editorHeader", // EVAL-E2E-018
  ]),
  // END EVAL claims

  // BEGIN COLLAB claims (append only; include the two-tenant walk id)
  COLLAB: Object.freeze([
    // T-COLLAB claims go here after acceptance.
  ]),
  // END COLLAB claims

  // BEGIN SRC claims (append only; include the two-tenant walk id)
  SRC: Object.freeze([
    // T-SRC claims go here after acceptance.
    // Shown so "Already running" sources can be reviewed. Not walked yet:
    // record the SRC walk id here when that walk is clean.
    "exp.new.runningSources",
  ]),
  // END SRC claims
});

/** @type {Readonly<Record<string, readonly string[]>>} */
export const MADE_REAL_PATHS = Object.freeze({
  L1: Object.freeze([]),
  L2: Object.freeze([]),
  L3: Object.freeze([]),
  L4: Object.freeze([]),
  L5: Object.freeze([]),
  L6: Object.freeze([]),
  // BEGIN XP paths (append only; include the two-tenant walk id)
  XP: Object.freeze([
    // T-XP paths go here after acceptance.
    "/model-hub/experiments/v2/{experiment_id}/resume/", // DATA-E2E-021
  ]),
  // END XP paths

  // BEGIN RES paths (append only; include the two-tenant walk id)
  RES: Object.freeze([
    // T-RES paths go here after acceptance.
  ]),
  // END RES paths

  // BEGIN IN paths (append only; include the two-tenant walk id)
  IN: Object.freeze([
    // T-IN paths go here after acceptance.
  ]),
  // END IN paths

  // BEGIN ORG paths (append only; include the two-tenant walk id)
  ORG: Object.freeze([
    // T-ORG paths go here after acceptance.
  ]),
  // END ORG paths

  // BEGIN GRID paths (append only; include the two-tenant walk id)
  GRID: Object.freeze([
    // T-GRID paths go here after acceptance.
    "/model-hub/r3/saved-views/", // DATA-E2E-049
    "/model-hub/r3/saved-views/{view_id}/", // DATA-E2E-049
  ]),
  // END GRID paths

  // BEGIN EVAL paths (append only; include the two-tenant walk id)
  EVAL: Object.freeze([
    // T-EVAL paths go here after acceptance.
    "/model-hub/eval-templates/create-dag/", // EVAL-E2E-072
    "/model-hub/eval-templates/{template_id}/dag/", // EVAL-E2E-072
    "/model-hub/eval-templates/dag/execute-adhoc/", // EVAL-E2E-072
    "/model-hub/eval-templates/{template_id}/dag/paths/", // EVAL-E2E-072
  ]),
  // END EVAL paths

  // BEGIN COLLAB paths (append only; include the two-tenant walk id)
  COLLAB: Object.freeze([
    // T-COLLAB paths go here after acceptance.
  ]),
  // END COLLAB paths

  // BEGIN SRC paths (append only; include the two-tenant walk id)
  SRC: Object.freeze([
    // T-SRC paths go here after acceptance.
  ]),
  // END SRC paths
});

/** Every surface id an integration group has made real. */
export const madeRealSurfaceIds = (slots = MADE_REAL) =>
  new Set(Object.values(slots).flat());

/** Every DSX_PROTOTYPE path template an integration group has backed. */
export const madeRealPathTemplates = (slots = MADE_REAL_PATHS) =>
  new Set(Object.values(slots).flat());
