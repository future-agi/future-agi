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
    // An experiment's name and description: the V2 create saves a typed
    // description, and an Edit's changed name or description is saved by its
    // own PUT (ExperimentUpdateV2), as the list's Rename does.
    "exp.rename", // EXP-E2E-009
  ]),
  // END XP claims

  // BEGIN RES claims (append only; include the two-tenant walk id)
  RES: Object.freeze([
    // Main accepted exactly the Summary and add-to-dataset Results walks.
    // Matrix, paths, traces, Iterate, eval-version impact and best-of remain
    // unclaimed; shared hosts gate their doors independently of Summary.
    "exp.results.addToDataset", // DSX-WALK-RES-21
    "exp.results.summary", // DSX-WALK-RES-51
    // A dataset row's runs across experiments: the Row view's Runs tab.
    "dataset.rowExtras.runs", // DATA-E2E-179
  ]),
  // END RES claims

  // BEGIN IN claims (append only; include the two-tenant walk id)
  IN: Object.freeze([
    // T-IN claims go here after acceptance.
    "dataset.addBlankRow", // DSX-WALK-IN-01
    "dataset.imports.blankGrid", // DSX-WALK-IN-02
    "dataset.upload", // DSX-WALK-IN-04
  ]),
  // END IN claims

  // BEGIN ORG claims (append only; include the two-tenant walk id)
  ORG: Object.freeze([
    // T-ORG claims go here after acceptance.
    // DATA-E2E-004: dataset name, description and tags from the list row and header.
    "dataset.settings",
    // DATA-E2E-020: persisted workspace folders, Organize and drag moves.
    "list.organize.folders",
    // DATA-E2E-006: typed metadata merge/delete and scoped readback.
    // Visibility is not proof: the live dataset_metadata capability gates saves.
    "list.organize.metadata",
    "dataset.duplicateMove", // DSX-WALK-ORG-51
    "dataset.list.columns", // DSX-WALK-ORG-52
    "dataset.switcher", // DSX-WALK-ORG-53
    "list.organize.star", // DSX-WALK-ORG-55
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
    // evals.remote stays unclaimed (main ruling t37): REMOTE-E2E-001 has no
    // flow spec or passing walk yet, so its five paths stay refused too.
    // The pass threshold set in a dataset evaluation's editor, saved on that
    // evaluation's run settings without saving a new version.
    "evals.editorPassThreshold", // EVAL-E2E-017
    // The evaluation editor's header naming the saved version configured.
    "evals.editorHeader", // EVAL-E2E-018
    "dataset.evalVersionNotice", // DSX-WALK-EVAL-02
    "dataset.evalVersions", // DSX-WALK-EVAL-03
    "dataset.experimentDefaults", // DSX-WALK-EVAL-04
    "evals.createOutputType", // DSX-WALK-EVAL-06
    "evals.groups", // DSX-WALK-EVAL-07
    "exp.new.evalCardChips", // DSX-WALK-EVAL-08
    "evals.agreement", // DSX-WALK-EVAL-81
    "exp.new.readsTable", // DSX-WALK-EVAL-84
    "exp.new.savedInputReads", // DSX-WALK-EVAL-85
    "palette.glossary", // DSX-WALK-EVAL-87
    "shell.notFound.page", // DSX-WALK-EVAL-88
    // evals.agreementTools (EVAL-E2E-083) stays unclaimed: Improve needs a
    // managed LLM route, and no stack activates one yet (model routes are
    // BYOK-only), so its controls stay hidden until a walk passes.
    // evals.agreement (claimed above) keeps the improve path it owns.
  ]),
  // END EVAL claims

  // BEGIN COLLAB claims (append only; include the two-tenant walk id)
  COLLAB: Object.freeze([
    // T-COLLAB claims go here after acceptance.
    // The workspace tag registry and an experiment's tags: the server serves
    // the list (with each experiment's tags), create, edit, delete and the
    // experiment's PUT, each scoped to the caller's workspace.
    "dataset.collab.tags", // EXP-E2E-010
  ]),
  // END COLLAB claims

  // BEGIN SRC claims (append only; include the two-tenant walk id)
  SRC: Object.freeze([
    // T-SRC claims go here after acceptance.
    // Shown so "Already running" sources can be reviewed.
    "exp.new.runningSources", // EXP-E2E-008
    "dataset.sdkRunTraces", // DSX-WALK-SRC-01
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
    // The copy dialog and its grid read; the new target has its own route.
    "/model-hub/datasets/{dataset_id}/rows/from-results/", // DSX-WALK-RES-21
    "/model-hub/datasets/new/rows/from-results/", // DSX-WALK-RES-21
    "/model-hub/datasets/{dataset_id}/results/", // DSX-WALK-RES-21
    // Summary and both hosts' run/baseline/config readers.
    "/model-hub/datasets/{dataset_id}/results/analysis/", // DSX-WALK-RES-51
    "/model-hub/datasets/{dataset_id}/runs/", // DSX-WALK-RES-51
    "/model-hub/datasets/{dataset_id}/rows/{row_id}/runs/", // DATA-E2E-179
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
    "/model-hub/datasets/{dataset_id}/star/", // DSX-WALK-ORG-55
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
    "/model-hub/eval-templates/{template_id}/agreement/", // EVAL-E2E-080
    // improve (EVAL-E2E-083) and the five remote paths (REMOTE-E2E-001) stay
    // refused until their walks pass (main ruling t37).
  ]),
  // END EVAL paths

  // BEGIN COLLAB paths (append only; include the two-tenant walk id)
  COLLAB: Object.freeze([
    // T-COLLAB paths go here after acceptance.
    "/model-hub/tags/", // EXP-E2E-010
    "/model-hub/tags/{tag_id}/", // EXP-E2E-010
    "/model-hub/experiments/v2/{experiment_id}/tags/", // EXP-E2E-010
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
