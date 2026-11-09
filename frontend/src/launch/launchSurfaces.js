// L6 launch switch (the capability contract): the typed registry of the DSX
// surfaces a NON-prototype build may show. A surface is one host (a page,
// a tab, a drawer, a door, a block of copy) with the inventory options it
// carries (options-truth/L7d, __fixtures__/inventory-classes.json) and every
// entry path that can open it: routes, deep-link keys, palette verbs, key
// shortcuts, nav entries and glossary terms. launchSwitch.js answers
// whether a surface shows; the gates read it in the files named by `gate`.
// Prototype builds never read this registry (prototype mode is unchanged).
// Pure data: no imports, so tests and the walk audit read it as is.

/**
 * @typedef {"shown"|"hidden"|"experimentsBasic"} Ships
 *   shown: REAL-TODAY on dev, ships as is.
 *   hidden: default hidden; a lane shows it by adding the id to its slot
 *     in madeReal.js once the option is real (with its proof).
 *   experimentsBasic: shows only while the experimentsBasic flag is ON
 *     (flipped at the Monday go/no-go, the capability contract).
 *
 * @typedef {object} LaunchRoute
 * @property {RegExp} re           full pathname
 * @property {string} sample       a pathname the tests and the walk try
 * @property {string} [paletteGate] separate NEW palette entry owner for a classic route
 * @property {string} fallback     where the hidden page lands; `$1` is the
 *                                 first capture (the dataset id)
 *
 * @typedef {object} LaunchSearchKey
 * @property {string} key          exact query key or a `*` glob
 * @property {string[]} [values]   only these values open the surface
 *                                 (absent: any value)
 * @property {string} [replace]    put this value instead of deleting
 * @property {RegExp} on           the pages (full pathname) where the key
 *                                 means this surface
 *
 * @typedef {object} LaunchEntries
 * @property {LaunchRoute[]} [routes]
 * @property {LaunchSearchKey[]} [search]
 * @property {string[]} [verbs]        palette command ids (exact, or a
 *                                     `prefix.*` glob)
 * @property {string[]} [shortcuts]    keymap `surface:action`, or
 *                                     `surface:*` for a whole sheet section
 * @property {string[]} [glossary]     glossary keys
 * @property {string[]} [tabs]         dataset page tabs (DevelopBar values)
 *
 * @typedef {object} LaunchSurface
 * @property {string} id
 * @property {string} label
 * @property {Ships} ships
 * @property {boolean} [experiments]   also needs experimentsBasic ON, even
 *                                     once a lane makes it real
 * @property {string} [parent]         hidden whenever the parent is hidden
 * @property {string[]} [anyOf]        a shared host: shows while any of these
 *                                     surfaces shows; never claimed itself
 * @property {string} gate             who hides it, where (`L6 file` or
 *                                     another lane's file it must gate)
 * @property {string} [madeRealBy]     the lane whose slot can show it
 * @property {string[]} options        inventory option ids (exact, or a
 *                                     `prefix:*` glob)
 * @property {LaunchEntries} [entries]
 * @property {string} [why]
 */

const UUIDISH = "[^/?#]+";

// Where a deep-link key means what the registry says (search rules apply
// only on these pages; the same key elsewhere is that page's own).
const RESERVED_DEVELOP =
  "experiment|individual-experiment|leaderboards|r4-lab|create-synthetic-dataset|edit-synthetic-dataset";
/** The dataset page (`/dashboard/develop/<id>`, any tab). */
export const ON_DATASET_PAGE = new RegExp(
  `^/dashboard/develop/(?!(?:${RESERVED_DEVELOP})(?:/|$))[^/?#]+/?$`,
);
/** The Evals pages. */
export const ON_EVALS = /^\/dashboard\/evaluations(\/|$)/;
/** Any page. */
export const ON_ANY = /^\//;

/** Row view tabs a launch build hides until their claims (ROW_TAB_SURFACES). */
export const ROW_EXTRA_TABS = Object.freeze([
  "runs",
  "activity",
  "conversation",
  "task",
  "trajectory",
]);

/** @type {LaunchSurface[]} */
export const LAUNCH_SURFACES = Object.freeze([
  {
    id: "dataset.sdkRunTraces",
    label: "SDK run traces on a dataset",
    madeRealBy: "SRC",
    ships: "hidden",
    gate: "sections/develop-detail/DevelopDetailView.jsx",
    options: [],
    entries: { search: [{ key: "run", on: ON_DATASET_PAGE }] },
    why: "Independent of experimentsBasic; default off until the SDK-run journey is qualified.",
  },
  // ------------------------------------------------------------ app shell
  {
    id: "shell.real",
    label: "App shell, settings, keys, evals, annotation, dataset grid basics",
    madeRealBy: "L6",
    ships: "shown",
    gate: "none (REAL-TODAY on origin/dev 6fedc779e1)",
    options: [],
    why: "Every REAL-TODAY option not listed under a hidden host below.",
  },
  {
    id: "shell.protoOnly",
    label:
      "Prototype-only worlds: stages, simulate verbs, dev pages, seeded copy",
    madeRealBy: "L6",
    ships: "hidden",
    gate: "prototype build flags (VITE_PROTOTYPE_MODE), unchanged",
    options: [
      "url:proto_*",
      "url:dev-pages",
      "url:observe-voice",
      "cmdk:*",
      "state:boot",
      "state:unmocked",
      "copy:*",
      "standin:*",
      "legacy:*",
      "stage:r5",
      "stage:voice-data",
      "sdk:waiting-copy",
      "sandbox:source",
      "where:menu",
      "where:platform",
      "task:*",
      "evals:end-state-note",
      "create:task-suite",
      "rowview:tab-task",
      "tools:*",
      "typed:add-tool",
      "card:code",
      "card:harness",
      "card:voice",
      "source:voice:providers",
      "change:agent-node:chips",
      "more:start-from:preset-link",
      "data:nothing-to-map",
      "wiz:folded-checks",
      "wiz:production-retest",
      "info:rerun-dialog:no-params",
      "info:rerun-failed:backoff-line",
      "action:rerun-with-changes:absent-kinds",
    ],
    entries: {
      routes: [
        {
          re: /^\/dashboard\/develop\/r4-lab(\/|$)/,
          sample: "/dashboard/develop/r4-lab",
          fallback: "/dashboard/develop",
        },
      ],
      // No simulation selector, including future keys, belongs in a launch URL.
      search: [
        { key: "proto_*", on: ON_ANY },
        { key: "rowTab", values: ["task"], on: ON_DATASET_PAGE },
      ],
    },
  },
  {
    id: "runners.connect",
    label: "Connected machines, live connection pills and the runners page",
    madeRealBy: "SRC",
    ships: "hidden",
    gate: "L6 sections/common/Connect/useConnect.js (polling unregistered)",
    options: [
      "mock:connect-endpoints",
      "connect:pill",
      "runners:page",
      "live:*",
      "identity:live-run-line",
      "identity:playback-tag",
      "strip:why-fails",
    ],
  },
  {
    id: "palette.runs",
    label: "Palette Runs group (the dataset's runs, R1)",
    ships: "hidden",
    experiments: true,
    gate: "L6 sections/common/CommandPalette/providers.js useRunCommands",
    madeRealBy: "RES",
    options: ["palette:search-results"],
    entries: { verbs: ["run.*"] },
  },
  {
    id: "palette.glossary",
    label: "Glossary terms of hidden features (palette, sheet)",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "L6 RowPanel/ShortcutSheet.jsx; CommandPalette.jsx (owed patch)",
    options: [],
    entries: {
      verbs: ["help.glossary", "glossary.*"],
      // Every term except the two that describe a shown grid control.
      glossary: ["*"],
    },
  },
  {
    id: "launch.fixes",
    label: "The switch itself and the copy it fixes on shown surfaces (L6)",
    ships: "shown",
    gate: "L6 src/launch/** and the files each option names",
    madeRealBy: "L6",
    options: [
      "proto-off:*",
      "cmdk-page:*",
      "sheet:shortcuts",
      "falcon:quick-actions",
      "state:errors",
      "key:help-sheet",
    ],
    why: "Made real by this lane: each one is a gate or a copy fix, proven by the launch walk.",
  },
  {
    id: "falcon.protoChip",
    label: "Falcon quick action that only the prototype answers",
    madeRealBy: "L6",
    ships: "hidden",
    gate: "L6 sections/falcon-ai/components/QuickActions.jsx",
    options: [],
  },
  {
    id: "falcon.dsx",
    label: "Falcon over DSX results (chips, sidebar, Ask Falcon on results)",
    madeRealBy: "RES",
    ships: "hidden",
    gate: "hosts gated per exp.results.* child",
    options: ["falcon:chips", "falcon:sidebar", "bulk:ask-falcon"],
  },
  {
    id: "errors.requestId",
    label: "Copy request id on error states (needs x-request-id exposed)",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "L6 sections/common/ux/ErrorState.jsx (shown when the header reads)",
    options: [],
    why: "origin/dev sets no CORS_EXPOSE_HEADERS; cross-origin the header is unreadable.",
  },
  {
    id: "shell.notFound",
    label: "Not-found and moved pages that name hidden features",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "routes (launchRoutes.js)",
    options: [
      "header:moved-banner",
      "url:owned-elsewhere",
      "url:pages",
      "url:grid-filters",
    ],
  },
  {
    id: "shell.notFound.page",
    label: "Not-found page for a missing or foreign dataset",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "none for the dataset page: DataTab/DatasetNotFound.jsx renders in every build. experiment-detail/ExperimentNotFound.jsx renders only on exp.classic routes (J09 no-hiding) and is outside this surface",
    options: ["state:not-found"],
    why: "A missing or another organization's dataset id answers DATASET_NOT_FOUND (400) and the page leads back to Datasets. The experiment not-found page is not covered here: it ships with exp.classic, and its missing and foreign experiment states are not verified by this surface.",
  },
  {
    id: "keys.ingestScopes",
    label: "Ingestion-only API key scope",
    madeRealBy: "ORG",
    ships: "hidden",
    gate: "keys/view/keyScopes.js (prototype-only already)",
    options: ["access:api-key-scopes"],
  },
  {
    id: "models.decision",
    label: "Decision models and their judges (vendor not integrated)",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "evals ModelSelector (prototype-only already); agentcc/decisions blocked",
    options: [
      "decision:*",
      "byok:keys-drawer",
      "editor:builtin-note",
      "model:your-models",
    ],
  },

  // ------------------------------------------------------------ datasets list
  {
    id: "list.real",
    label:
      "Datasets list on develops/get-datasets/ (name search, sort, paging)",
    ships: "hidden",
    gate: "L1 sections/develop/DevelopView.jsx (its adapter)",
    madeRealBy: "L1",
    options: [
      "nav:datasets",
      "list:open",
      "list:search",
      "list:delete",
      "list:keyboard",
      "list:first-run-hint",
      "list:selection:compare",
      "list:row-menu",
    ],
    why: "The list reads the absent datasets/query/ until L1 lands; the nav entry stays (it is dev's).",
  },
  {
    id: "list.organize.folders",
    label: "Dataset folders and moves",
    ships: "hidden",
    gate: "develop/realBackendCaps.js listFolders; organize/organizeItems.js",
    options: [
      "org:drag-to-folder",
      "org:folder-actions",
      "org:folder-rail",
      "org:organize",
      // Plan F41: folder access gates belong with folders.
      "access:gates",
    ],
    madeRealBy: "ORG",
  },
  {
    id: "list.organize.star",
    label: "Star datasets",
    ships: "hidden",
    gate: "develop/realBackendCaps.js listStars",
    options: ["org:star"],
    madeRealBy: "ORG",
  },
  {
    id: "list.organize.query",
    label: "Dataset list facets, group by and saved views",
    ships: "hidden",
    gate: "develop/realBackendCaps.js listFilters, listGroupBy, listSavedViews, listRunningPoll",
    options: [
      "org:filter",
      "org:group-by",
      "org:saved-list-views",
      "list:view",
    ],
    madeRealBy: "ORG",
  },
  {
    id: "dataset.list.columns",
    label: "Dataset Owner, Source and Updated columns",
    ships: "hidden",
    gate: "develop/realBackendCaps.js listColumns; datasetList.js; datasetListColumns.jsx",
    madeRealBy: "ORG",
    options: [],
  },
  {
    id: "list.organize.metadata",
    label: "Dataset metadata editor and bulk changes",
    ships: "hidden",
    gate: "organize/organizeItems.js (Metadata… door)",
    options: [],
    madeRealBy: "ORG",
  },
  {
    id: "list.organize.agents",
    label: "Attach and detach datasets to agent graphs",
    ships: "hidden",
    gate: "organize/organizeItems.js (Agents… door)",
    options: [],
    madeRealBy: "ORG",
  },

  // ------------------------------------------------------------ dataset page
  {
    id: "dataset.tab.summary",
    label: "Dataset Summary enhancements (R5 analysis)",
    madeRealBy: "RES",
    ships: "hidden",
    gate: "L6 DevelopDetailView.jsx (tab coerced) + DevelopBar.jsx (owed patch)",
    options: [
      "sum:ask-falcon",
      "sum:baseline",
      "sum:choose-winner",
      "sum:classification-card",
      "sum:compare-with",
      "sum:explore",
      "sum:export",
      "verdict:*",
      "na:*",
      "banner:verdict",
      "bydataset:view",
      "print:view",
    ],
  },
  {
    id: "dataset.tab.experiments",
    label: "Dataset Experiments tab (the DSX leaderboard over R1)",
    ships: "shown",
    experiments: true,
    gate: "L6 DevelopDetailView.jsx (tab coerced) + DevelopBar.jsx (owed patch)",
    madeRealBy: "L3",
    options: [
      "leaderboard:*",
      "lb:*",
      "kept:*",
      "ledger:*",
      "identity:*",
      "status:*",
      "runtray:*",
      "after:*",
      "chip:menu",
      "cfg:diff",
      "board:*",
      "exp:ordinal-chip",
      "entry:empty-state-doors",
      "entry:leaderboard-not-run-here",
      "action:leaderboard:*",
      "action:create-toast-undo",
      "action:duplicate-edit",
      "action:open-in-prompts-or-playground",
      "action:run-prompt:save-template",
      "action:workbench:run",
      "handoff:*",
      "metric:*",
      "tts:voices",
      "upload:how-to-upload",
      "banner:publish-and-set-baseline",
      "banner:undo-baseline",
    ],
    entries: {
      shortcuts: ["leaderboard:*", "experiments:*"],
      verbs: ["leaderboard.*", "experiments.*"],
    },
  },
  {
    id: "dataset.versions",
    label: "Dataset versions, pending changes, Save version, history",
    ships: "hidden",
    gate: "L1 DataTab (classic write mode); L6 header chip + H / Cmd+S keys; L6 DataStep.jsx version picker",
    options: [
      "versions:*",
      "data:version",
      "data:unsaved-rows",
      "door:run-on-rows:version-history",
      "status:set",
    ],
    entries: {
      shortcuts: ["grid:history", "row:saveVersion"],
      verbs: [
        "versions.*",
        "grid.history",
        "row.history",
        "row.saveVersion",
        "version.*",
        "selection.restoreRows",
      ],
      search: [
        {
          key: "history",
          on: ON_DATASET_PAGE,
        },
        {
          key: "version",
          on: ON_DATASET_PAGE,
        },
        {
          key: "hcmp",
          on: ON_DATASET_PAGE,
        },
        {
          key: "version_id",
          on: ON_DATASET_PAGE,
        },
      ],
    },
    madeRealBy: "GRID",
  },
  {
    id: "dataset.schema",
    label: "Dataset schema and enforcement",
    ships: "hidden",
    gate: "DevelopBarRightSection/DevelopDataRightSection.jsx Schema door",
    options: ["schema:*"],
    madeRealBy: "GRID",
  },
  {
    id: "dataset.splits",
    label: "Dataset splits and training-set export",
    madeRealBy: "GRID",
    ships: "hidden",
    gate: "L1 DataTab; L6 S key on the Row view; L6 DataStep.jsx split chips; Splits/ManageSplitsDialog.jsx (TrainingSetButton)",
    options: [
      "splits:*",
      "grid:split-filter",
      // Plan F50: the training-set export lives in Manage splits.
      "training:export",
    ],
    entries: {
      shortcuts: ["row:splits"],
      // L6 verify 2 (latent verb): the Row view's Change splits.
      verbs: ["row.splits"],
      // `splits` filters the grid; `split` belongs to experiment run scope.
      search: [{ key: "splits", on: ON_DATASET_PAGE }],
    },
  },
  {
    id: "dataset.savedViews",
    label: "Saved views and layouts on the grid",
    madeRealBy: "GRID",
    ships: "hidden",
    gate: "L6 DevelopDataRightSection.jsx (View menu without Saved views)",
    options: [
      "grid:saved-views",
      "layouts:*",
      "views:*",
      "cmd:view",
      "ex:saved-views",
    ],
    entries: {
      search: [
        { key: "view", on: ON_DATASET_PAGE },
        { key: "grid_view", on: ON_DATASET_PAGE },
      ],
    },
  },
  {
    id: "dataset.export",
    label:
      "Download / export a dataset (row menu, grid More > Download, Export dialog scope)",
    ships: "hidden",
    gate: "NONE YET: no code reads this id (the list row menu omits Download, REAL_ROW_ACTIONS; ExportDialog's Version/Rows scope); wire a gate before claiming",
    madeRealBy: "GRID",
    // Plan F47 (GRID's first packet).
    options: ["export:dataset"],
  },
  {
    id: "dataset.filtersSystem.pills",
    label: "Failed-only filters and system pills",
    ships: "hidden",
    gate: "DevelopBarRightSection/DevelopBarLeftSection.jsx FailedOnlyChip; launchSearch query rules",
    options: ["grid:failed-only", "grid:system-pills"],
    entries: {
      search: [
        {
          key: "failed",
          on: ON_DATASET_PAGE,
        },
        {
          key: "fevals",
          on: ON_DATASET_PAGE,
        },
      ],
    },
    madeRealBy: "GRID",
  },
  {
    id: "dataset.filtersSystem.expressions",
    label: "Expression filters and custom columns",
    ships: "hidden",
    gate: "DevelopBarRightSection/DevelopBarLeftSection.jsx ExpressionFilterButton; launchSearch expr rule",
    // Plan F51 groups md:reason with the expression filters.
    options: ["grid:filter-expression", "expr:*", "col:*", "md:reason"],
    entries: {
      search: [
        {
          key: "expr",
          on: ON_DATASET_PAGE,
        },
      ],
    },
    madeRealBy: "GRID",
  },
  {
    id: "dataset.collab.comments",
    label: "Row collaboration: comments",
    ships: "hidden",
    gate: "DataGrid/gridContextMenu.js; RowFieldsTab.jsx; RowCollabPills.jsx; RowActivityTab.jsx (threads, composer); resultsCollabActions.js",
    // Plan F56 lists review:summary with the comments.
    options: ["comments:*", "cmd:actionbar", "review:summary"],
    entries: {
      // The comment doors' keys (commentDeepLink); `rowTab=activity` is
      // the shared host's (dataset.rowTab.activity), `ract` the history's.
      search: [
        { key: "rcs", on: ON_DATASET_PAGE },
        { key: "rcompose", on: ON_DATASET_PAGE },
        { key: "ranchor", on: ON_DATASET_PAGE },
      ],
    },
    madeRealBy: "COLLAB",
  },
  {
    id: "dataset.collab.assignments",
    label: "Row collaboration: assignments",
    ships: "hidden",
    gate: "DataGrid/gridContextMenu.js; RowCollabPills.jsx; resultsCollabActions.js; results/SelectionBar.jsx",
    options: ["assign:*"],
    madeRealBy: "COLLAB",
  },
  {
    id: "dataset.collab.tags",
    label: "Row collaboration: tags",
    ships: "hidden",
    gate: "NONE YET: no code reads this id (Tag rows… is gated by datasetCaps versionedActions, DevelopDataSelectionActive.jsx); wire a gate before claiming",
    options: ["tags:*", "bulk:tag-rows"],
    madeRealBy: "COLLAB",
  },
  {
    id: "dataset.collab.corrections",
    label: "Row collaboration: corrections",
    ships: "hidden",
    gate: "NONE YET: no code reads this id (corrections live in develop-detail/Review/*); wire a gate before claiming",
    options: [
      "corrections:*",
      "review:corrections-option",
      "review:annotate:correct-expected",
    ],
    madeRealBy: "COLLAB",
  },
  {
    id: "dataset.collab.translate",
    label: "Row collaboration: translate",
    ships: "hidden",
    gate: "DataGrid/gridContextMenu.js; RowFieldsTab.jsx",
    options: ["translate:*"],
    entries: { search: [{ key: "rtranslate", on: ON_DATASET_PAGE }] },
    madeRealBy: "COLLAB",
  },
  {
    id: "dataset.collab.reviews",
    label: "Row collaboration: outside reviewers and pairwise review",
    ships: "hidden",
    gate: "experiment-detail/results/SelectionBar.jsx (pairwise); review hosts require their own adapter",
    options: [
      "review:outside",
      "review:outside-page",
      "rules:outside-page",
      "rules:rubric-card",
      "rules:done-means-done",
      "review:qa-role-split",
      "bulk:review-pairwise",
      "drawer:pairwise",
    ],
    madeRealBy: "COLLAB",
  },
  {
    id: "dataset.collab.sendToQa",
    label: "Send to QA: result rows to an annotation queue with a rubric",
    ships: "hidden",
    gate: "experiment-detail/results/resultsCollabActions.js + SelectionBar.jsx (StartReviewDialog)",
    // Plan F55: starting, submitting and the results' review tab.
    options: [
      "qa:send-to-qa",
      "review:start",
      "review:submit",
      "review:results-review-tab",
    ],
    madeRealBy: "COLLAB",
  },
  {
    id: "dataset.rowExtras.runs",
    label: "Row runs across experiments",
    ships: "hidden",
    gate: "DatapointDrawerV2/RowView.jsx reads and tabs; ROW_TAB_SURFACES",
    options: [
      "rowview:tabs",
      "rowview:output-rerun",
      "rowview:eval-detail",
      "find:result",
    ],
    entries: {
      shortcuts: ["row:tab"],
      search: [
        {
          key: "rowTab",
          values: ["runs"],
          on: ON_DATASET_PAGE,
        },
        {
          key: "run",
          on: ON_DATASET_PAGE,
        },
        {
          key: "rshow",
          on: ON_DATASET_PAGE,
        },
        {
          key: "rsort",
          on: ON_DATASET_PAGE,
        },
      ],
    },
    madeRealBy: "RES",
  },
  {
    id: "dataset.rowApiPath",
    label: "Row view API path and API call copy",
    ships: "hidden",
    gate: "DatapointDrawerV2/rowApiPath.js (RowIdChip.jsx)",
    options: ["rowview:open-nav"],
    madeRealBy: "GRID",
  },
  {
    id: "dataset.rowExtras.activity",
    label: "Row activity history",
    ships: "hidden",
    gate: "DatapointDrawerV2/RowActivityTab.jsx (history feed and filter chips); the tab is dataset.rowTab.activity",
    options: [],
    // `ract` is the history's filter chip (RowActivityTab, useRowViewUrl).
    entries: { search: [{ key: "ract", on: ON_DATASET_PAGE }] },
    madeRealBy: "COLLAB",
  },
  {
    // A shared host, never claimed: the Row view's Activity tab shows while
    // comments or the activity history shows, and each part inside it keeps
    // its own gate (RowActivityTab.jsx). Every comment door opens this tab.
    id: "dataset.rowTab.activity",
    label: "Row view Activity tab (hosts comments and the activity history)",
    ships: "hidden",
    anyOf: ["dataset.collab.comments", "dataset.rowExtras.activity"],
    gate: "DatapointDrawerV2/RowView.jsx tabs via ROW_TAB_SURFACES",
    madeRealBy: "COLLAB",
    options: [],
    entries: {
      search: [{ key: "rowTab", values: ["activity"], on: ON_DATASET_PAGE }],
    },
  },
  {
    id: "dataset.rowExtras.conversation",
    label: "Row conversation and trajectory",
    ships: "hidden",
    gate: "DatapointDrawerV2/RowView.jsx kind tabs; ROW_TAB_SURFACES",
    options: ["rowview:tab-conversation", "rowview:tab-trajectory"],
    entries: {
      search: [
        {
          key: "rowTab",
          values: ["conversation", "trajectory"],
          on: ON_DATASET_PAGE,
        },
      ],
    },
    madeRealBy: "GRID",
  },
  {
    id: "dataset.gridWrites",
    label:
      "Grid writes in classic mode (cell edit, selection actions, multimodal)",
    ships: "hidden",
    gate: "L1 DataTab/DevelopDataV2.jsx",
    madeRealBy: "L1",
    options: [
      "grid:cell-edit",
      "grid:selection-actions",
      "grid:context-menu",
      "stage:multimodal-data",
      "data:rows:all",
    ],
  },
  {
    id: "dataset.columnRole",
    label: "Static column role, column menu, draft column",
    ships: "hidden",
    gate: "L1 DataTab (the role controls)",
    madeRealBy: "L4",
    options: ["grid:add-column-static", "grid:column-menu", "draft:*"],
  },
  {
    id: "dataset.settings",
    label:
      "Dataset settings: name, description and tags (edit_dataset_behavior)",
    madeRealBy: "ORG",
    ships: "hidden",
    gate: "L6 DevelopBar/DatasetHeaderBar.jsx; develop/DevelopView.jsx (row Edit details, datasetCaps().settings)",
    options: ["header:settings"],
    entries: { verbs: ["dataset.configure"] },
    why: "Shown in a launch build once ORG claims it. Sharing and access controls are separate (dataset.share). L4 made column roles real (dataset.columnRole).",
  },
  {
    id: "dataset.share",
    label: "Share dialog: access, sharing policy, public links",
    madeRealBy: "COLLAB",
    ships: "hidden",
    gate: "L6 DevelopBar/DatasetHeaderBar.jsx",
    options: [
      "header:share",
      "share:policy",
      "share:public",
      "share:public-page",
      // Plan F62's access rows; access:gates is folders' (F41) and
      // access:api-key-scopes is keys.ingestScopes' (F78).
      "access:403-note",
      "access:audit-button",
      "access:delete-needs-manage",
      "access:grant",
      "access:request",
      "access:section",
      "access:viewer-ceiling",
      "access:ws-level",
    ],
    entries: { verbs: ["dataset.share"] },
  },
  {
    id: "dataset.audit",
    label: "Dataset and workspace audit log",
    madeRealBy: "COLLAB",
    ships: "hidden",
    gate: "L6 DatasetHeaderBar.jsx; routes; nav (prototype-only already)",
    options: ["audit:*", "nav:audit-log"],
    entries: {
      verbs: ["dataset.auditLog"],
      routes: [
        {
          re: /^\/dashboard\/settings\/workspace\/[^/]+\/audit-log(\/|$)/,
          sample: "/dashboard/settings/workspace/w1/audit-log",
          fallback: "/dashboard/settings/workspace",
        },
      ],
    },
  },
  {
    id: "dataset.fromProduction",
    label: "Add production failures (?add=production)",
    madeRealBy: "IN",
    ships: "hidden",
    gate: "L6 DataTab/dataAccessGate.js + DatasetHeaderBar.jsx",
    options: ["create:from-production"],
    entries: {
      search: [{ key: "add", values: ["production"], on: ON_DATASET_PAGE }],
      // Datasets list palette (DevelopView) and the Add rows tile.
      verbs: ["datasets.fromTraces"],
    },
  },
  {
    id: "dataset.imports.generateRowsTile",
    label: "Generate rows in Add rows",
    ships: "hidden",
    gate: "ADD_ROWS_TILE_SURFACE; AddRowDrawer.jsx",
    options: ["synthetic:generate-rows"],
    madeRealBy: "IN",
  },
  {
    id: "dataset.imports.sources",
    label: "Saved import sources, refresh and source chips",
    ships: "hidden",
    gate: "DevelopBar/DatasetHeaderBar.jsx source chips; ExistingDatasetModal.jsx preview",
    options: [
      "import:*",
      "rows:*",
      "review:add-corrected-items",
      "snippet:import-api-call",
      "header:more",
      "absent:sdk-doors",
      "create:copy-from-dataset",
      "create:drawer",
    ],
    entries: { verbs: ["grid.importRows"] },
    madeRealBy: "IN",
  },
  {
    // DSX J09 / 767/769/770: legacy manual creation is not a new feature.
    id: "dataset.imports.blankGrid",
    label: "Start with named and typed columns",
    ships: "shown",
    gate: "develop/importOptions.js createDoorShown",
    options: ["create:blank-grid"],
    madeRealBy: "IN",
  },
  {
    id: "dataset.imports.columnRoles",
    label: "File import column roles",
    ships: "hidden",
    gate: "AddDatasetDrawer/uploadBody.js uploadAccepts + generated contract",
    options: [],
    madeRealBy: "IN",
  },
  {
    id: "dataset.imports.transforms",
    label: "File import flatten and JSON transforms",
    ships: "hidden",
    gate: "AddDatasetDrawer/uploadBody.js uploadAccepts + generated contract",
    options: [],
    madeRealBy: "IN",
  },
  {
    id: "dataset.addRows",
    label: "Add rows sheet: legacy file upload, dataset copy and empty rows",
    // Census G2: these doors predate 2.0.0. Availability is separate:
    // copy-in works; file import and pending blank rows are visibly disabled.
    ships: "shown",
    gate: "AddRowDrawer.jsx tiles + importOptions.js rowImportOption availability",
    madeRealBy: "IN",
    options: [
      "rows:add-rows-sheet",
      "rows:upload-file",
      "rows:copy-from-dataset",
    ],
  },
  {
    id: "dataset.imports.pasteRows",
    label: "Paste rows into an existing dataset",
    // NEW in 2.0.0: never inherit the legacy sheet's no-hiding exception.
    ships: "hidden",
    gate: "AddRowDrawer.jsx paste tile",
    madeRealBy: "IN",
    options: ["rows:paste"],
  },
  {
    id: "dataset.addBlankRow",
    label: "Add a blank row (a pending change until L1's classic write)",
    ships: "hidden",
    gate: "L6 palette grid.addRow (DevelopDataRightSection addBlank; L1 owed 01 writes it at once)",
    madeRealBy: "IN",
    options: ["rows:blank-row"],
    entries: { verbs: ["grid.addRow"] },
  },
  {
    id: "dataset.upload",
    label: "Create from a local file (the 400 fix)",
    ships: "hidden",
    gate: "L2 UploadFileModal.jsx (strict body)",
    madeRealBy: "IN",
    options: ["create:upload-file"],
  },
  {
    id: "dataset.switcher",
    label: "Switch dataset picker over the list query",
    ships: "hidden",
    gate: "L1 (reads the list adapter)",
    madeRealBy: "ORG",
    options: ["header:switch-dataset"],
  },
  {
    id: "dataset.duplicateMove",
    label: "Copy or move a dataset between workspaces, with a preview",
    ships: "hidden",
    gate: "develop/realBackendCaps.js duplicateMove; DuplicateDataset/DatasetTransferDialog.jsx",
    madeRealBy: "ORG",
    options: ["copy:duplicate-move"],
  },
  {
    id: "dataset.runPromptEdit",
    label: "Update and Run on Run Prompt and generated columns (scope)",
    ships: "hidden",
    gate: "develop-detail/AddColumn/rerunScope.js + generated body contract",
    madeRealBy: "GRID",
    options: [
      "knob:run-prompt:model",
      "knob:run-prompt:prompt",
      "knob:run-prompt:concurrency",
      "dyncol:*",
    ],
  },
  {
    id: "dataset.evalVersions",
    label: "Eval version pin and saved versions",
    ships: "hidden",
    gate: "L4 / L5 backend fields",
    madeRealBy: "EVAL",
    options: [
      "ver:pin-card",
      "editor:version-select",
      "editor:add-update",
      "editor:save-version",
    ],
  },
  {
    id: "dataset.evalScoring",
    label: "Dataset eval scoring semantics",
    ships: "hidden",
    gate: "common/EvaluationDrawer/SavedEvalsList.jsx scoringFor",
    madeRealBy: "EVAL",
    options: ["dsdrawer:scoring"],
  },
  // L5's options, split from dataset.evalVersions (L5 merge notes 1b) so
  // each lane claims only what its own walk proved.
  {
    id: "dataset.evalVersionNotice",
    label: "Eval list: version that runs, newest version, pass rule",
    ships: "hidden",
    gate: "L5 backend fields (get_evals_list)",
    madeRealBy: "EVAL",
    options: ["ver:saved-list-notice"],
  },
  // The New experiment wizard's eval card chips, split from
  // dataset.evalVersionNotice so a claim on the dataset eval list notice
  // never counts the wizard chips as shown.
  {
    id: "exp.new.evalCardChips",
    label: "Wizard eval card chips: output type, pass bar, judge, version",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "StepperComponentExperiment/EvaluationStepExperimentCreation.jsx EvalCard isSurfaceShown('exp.new.evalCardChips')",
    why: "General chips, including Auto and Decision, stay hidden until independently walked. The Default badge is outside this wrapper and belongs only to dataset.experimentDefaults (F67 / EVAL-E2E-011).",
    madeRealBy: "EVAL",
    options: ["wiz:card-chips"],
  },
  {
    id: "dataset.experimentDefaults",
    label: "Use in new experiments (dataset eval defaults)",
    ships: "shown",
    // The switch only preselects evals in the New experiment wizard.
    experiments: true,
    gate: "L5 SavedEvalsList.jsx defaultSwitchFor; StepperComponentExperiment/EvaluationStepExperimentCreation.jsx eval-default-chip via isSurfaceShown('dataset.experimentDefaults')",
    why: "F67 ON claims only this defaults surface, never exp.new.evalCardChips. EVAL-E2E-011 must show the Default badge while asserting the general chips wrapper remains absent.",
    madeRealBy: "EVAL",
    options: ["dsdrawer:use-in-new-experiments", "wiz:manage"],
  },
  {
    id: "hf.revision",
    label: "Hugging Face Revision field (400 on dev's strict config route)",
    ships: "hidden",
    gate: "L6 sections/huggingface/HuggingFaceView.jsx",
    madeRealBy: "IN",
    options: ["hf:drawer"],
  },
  {
    id: "hf.curated",
    label: "Hugging Face 'Curated benchmarks first' (does nothing on dev)",
    ships: "hidden",
    gate: "L6 sections/huggingface/HuggingFaceView.jsx",
    madeRealBy: "IN",
    options: [],
  },
  {
    // DSX J09 / 767/769/770: pre-existing HF doors and route stay visible,
    // but disabled with a GE-2 notice. A 200 creates rows before the worker
    // fills their cells; a worker without the `ml` extra cannot supply values.
    // Enablement still needs a-hf-create-values-load on an ml-extra image.
    id: "hf.create",
    label: "Hugging Face: create a dataset from a Hub dataset",
    ships: "shown",
    gate: "L6 sections/develop/importOptions.js LAUNCH_HIDDEN_CREATE + the /dashboard/huggingface route",
    madeRealBy: "IN",
    options: ["create:hf"],
    entries: {
      routes: [
        {
          re: /^\/dashboard\/huggingface(\/|$)/,
          sample: "/dashboard/huggingface",
          fallback: "/dashboard/develop",
        },
      ],
    },
  },
  {
    // Same worker and GE-2 disable as create; visibility is not enablement.
    id: "hf.addToExisting",
    label: "Hugging Face Add to existing dataset (toast reads 0 pending rows)",
    ships: "shown",
    gate: "L6 sections/huggingface/HuggingFaceView.jsx; AddRowDrawer.jsx tile",
    madeRealBy: "IN",
    options: ["rows:hf"],
  },
  {
    id: "synthetic.extras",
    label:
      "Generate more like these (selected rows) and knowledge-base goldens",
    madeRealBy: "IN",
    ships: "hidden",
    gate: "NONE YET: no code reads this id (the selection's Generate more like these sits under datasetCaps selectionVersionedActions); wire a gate before claiming",
    // Plan F37.
    options: ["synthetic:generate-more-like-these", "synthetic:kb-goldens"],
  },

  // ------------------------------------------------------------ experiments
  // J09 feature-age REVISED / 767: v1.56.1 entry points, not DSX enhancements.
  // Census A/B moves legacy controls here; NEW children, palette and keys stay OFF.
  {
    id: "exp.classic",
    label: "Classic experiments and dataset Evals/Prompt summaries",
    madeRealBy: "XP",
    ships: "shown",
    gate: "DevelopDetailView, NewExperimentButton, DatasetListCells and classic experiment hosts",
    options: [
      "entry:new-experiment-button",
      "card:prompt",
      "knob:prompt:version",
      "knob:prompt:models",
      "knob:prompt:model-params",
      "knob:prompt:variables",
      "exp:name",
      "exp:output-format",
      "wiz:add-evaluation",
      "wiz:card-edit",
      "wiz:card-remove",
      "wiz:edit-experiment-evals",
      "wiz:eval-step",
      "sum:all-runs",
    ],
    entries: {
      tabs: ["experiments", "summary"],
      search: [{ key: "new_experiment", on: ON_DATASET_PAGE }],
      routes: [
        {
          re: new RegExp(
            `^/dashboard/develop/experiment/${UUIDISH}(/(data)?)?/?$`,
          ),
          paletteGate: "exp.page",
          sample: "/dashboard/develop/experiment/e1/data",
          fallback: "/dashboard/develop",
        },
        {
          re: new RegExp(
            `^/dashboard/develop/individual-experiment/${UUIDISH}(/(data|summary))?/?$`,
          ),
          paletteGate: "exp.page",
          sample: "/dashboard/develop/individual-experiment/e1/data",
          fallback: "/dashboard/develop",
        },
        {
          re: new RegExp(
            `^/dashboard/develop/experiment/${UUIDISH}/summary/?$`,
          ),
          paletteGate: "exp.results.summary",
          sample: "/dashboard/develop/experiment/e1/summary",
          fallback: "/dashboard/develop",
        },
      ],
    },
  },
  {
    id: "exp.new",
    label: "New experiment enhancements, palette verbs and advanced deep links",
    madeRealBy: "XP",
    ships: "shown",
    // All-on flip: shown behind the flag, so an explicit false build keeps it off.
    experiments: true,
    gate: "CreateAdmissionNotice.jsx; advanced children and palette/search gates",
    options: [
      "entry:kind-menu",
      "entry:deep-link",
      "entry:cmdk-verbs",
      "entry:run-on-rows:*",
      "door:run-on-rows:*",
      "knob:prompt:add-tool",
      "knob:prompt:max-tool-turns",
      "knob:prompt:tool-modes",
      "knob:agent:version",
      "knob:sample",
      "knob:error-policy",
      "knob:metadata",
      "knob:trial-reducer",
      "knob:record",
      "knob:limits:*",
      "knob:cost-line",
      "wiz:card-chips",
      "wiz:card-mapping-preview",
      "wiz:empty-reference-warning",
      "wiz:expected-answers",
      "wiz:folded-checks",
      "wiz:manage",
      "wiz:production-retest",
      "wiz:reads-table",
      "wiz:review-evals",
      "wiz:sample-row",
      "picker:*",
      "card:agent",
      "vars:*",
      "mapping:*",
      "model:turing",
      // The wizard's Review step only: the human-review options named
      // review:* belong to their COLLAB / IN claims.
      "review:compare-table",
      "review:concurrency",
      "review:empty-input-warning",
      "review:headline",
      "review:modality-warning",
      "review:planned-ledger",
      "review:rows-send",
      "review:run-lines",
      "review:summary-cards",
      "review:trials",
      "review:worst-math",
      "estimate:*",
      "cost:*",
      "exp:compact-head-edit",
      "exp:description",
      "exp:ordinal-chip",
      "more:*",
      "typed:*",
      "data:rows:*",
      "data:replay-tools",
      "editor:allow-na",
      "evalagent:*",
      "action:start-from",
      "info:review:own-tries",
      "test:card",
    ],
    entries: {
      search: [
        { key: "newExperiment", on: ON_DATASET_PAGE },
        { key: "source", on: ON_DATASET_PAGE },
        { key: "entry", on: ON_DATASET_PAGE },
        { key: "src", on: ON_DATASET_PAGE },
        { key: "rows", on: ON_DATASET_PAGE },
        { key: "form", on: ON_DATASET_PAGE },
        { key: "agent", on: ON_DATASET_PAGE },
        { key: "mode", on: ON_DATASET_PAGE },
        { key: "lang", on: ON_DATASET_PAGE },
        { key: "prompt_id", on: ON_DATASET_PAGE },
        { key: "prompt_version", on: ON_DATASET_PAGE },
      ],
      verbs: ["dataset.newExperiment", "newexp.*", "experiment.new*"],
      shortcuts: ["newExperiment:*"],
    },
  },
  {
    id: "exp.new.kindMenu",
    label: "New experiment ▾ kinds (agent graph, already running)",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "L6 NewExperimentButton.jsx",
    madeRealBy: "XP",
    options: [],
  },
  {
    id: "exp.new.runningSources",
    label: "Already running sources: HTTP endpoints, A2A agents, MCP tools",
    madeRealBy: "SRC",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "L6 RunSources/catalog.js, SourcePicker.jsx, runOnRows.js",
    options: [
      "knob:http:*",
      "knob:a2a:*",
      "knob:mcp:*",
      "action:http:*",
      "action:a2a:*",
      "action:mcp:*",
      "source:running:*",
      "examples:*",
      "card:running",
      "a2a:*",
      "edit:a2a-mcp-as-http",
    ],
    entries: {
      search: [
        {
          key: "entry",
          values: ["http", "a2a", "mcp_tool"],
          on: ON_DATASET_PAGE,
        },
      ],
    },
  },
  {
    id: "exp.rename",
    label: "Rename an experiment and edit its description",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "wizardSettings.js description (ConfigureStepExperimentCreation.jsx); the list's Rename (RENAME_NOT_AVAILABLE) needs its gate before claiming",
    madeRealBy: "XP",
    // Plan F03.
    options: ["exp:description"],
  },
  {
    id: "exp.new.splitScope",
    label: "Run experiments on dataset splits",
    ships: "hidden",
    madeRealBy: "XP",
    gate: "DataStep.jsx, CostEstimateLine.jsx, RunExperiment.jsx, RunOnRowsButton.jsx",
    options: [
      "data:rows:split",
      "estimate:smoke-split-suggestion",
      "entry:run-on-rows:training-split",
    ],
    entries: { search: [{ key: "split", on: ON_DATASET_PAGE }] },
  },
  {
    id: "exp.new.scope",
    label:
      "New experiment scope: picked rows, sample, per-row limits, error policy, metadata",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "L6 ReviewStep.jsx (scope lines, error policy), DataStep.jsx + SampleSetting.jsx (these rows, sample), RunSettings/RowLimits.jsx (limits per row), row doors (RunOnRowsButton, RowView, RowViewBody); L2 blocks Run",
    madeRealBy: "XP",
    options: [
      "knob:sample",
      "knob:error-policy",
      "knob:metadata",
      "knob:limits:*",
      "review:headline",
      "review:run-lines",
      "review:compare-table",
      "review:rows-send",
      "review:modality-warning",
      "review:empty-input-warning",
      "data:rows:these",
      "data:rows:refs-note",
      "estimate:spend-line",
    ],
  },
  {
    id: "exp.new.runSettings",
    label:
      "New experiment run settings: trials and reducers, concurrency, cost caps",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "L6 ReviewStep.jsx (trials, concurrency, worst-case math, planned ledger); Experiment/costEstimateModel.js",
    madeRealBy: "XP",
    options: [
      "knob:trial-reducer",
      "typed:trials",
      "review:trials",
      "review:concurrency",
      "review:worst-math",
      "review:planned-ledger",
    ],
  },
  {
    id: "exp.new.record",
    label: "New experiment record: record a run, start from a previous run",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "L6 ReviewStep.jsx (RecordToggle); ConfigureStepExperimentCreation.jsx (More settings, wizardSettings.js)",
    madeRealBy: "XP",
    options: [
      "knob:record",
      "more:*",
      "action:start-from",
      "info:review:own-tries",
    ],
  },
  {
    id: "exp.new.readsTable",
    label: "Wizard reads table (eval-playground reads_only: would bill judges)",
    madeRealBy: "EVAL",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "StepperComponentExperiment/EvaluationStepExperimentCreation.jsx EvalCard",
    options: ["wiz:card-mapping-preview"],
  },
  {
    id: "exp.new.savedInputReads",
    label: "Wizard saved-input preview for single evaluations",
    madeRealBy: "EVAL",
    ships: "hidden",
    parent: "exp.new",
    experiments: true,
    gate: "StepperComponentExperiment/ReadsOnSampleTable.jsx",
    options: ["wiz:reads-table"],
  },
  {
    id: "exp.page",
    label: "Experiment page palette destinations",
    madeRealBy: "XP",
    ships: "experimentsBasic",
    gate: "CommandPalette/commandRegistry.js; classic routes belong to exp.classic",
    options: [],
    entries: {},
  },
  {
    id: "exp.runControl",
    label: "Stop and resume experiment runs",
    ships: "hidden",
    experiments: true,
    madeRealBy: "XP",
    gate: "common/ExperimentRunControl/ExperimentRunControl.jsx",
    options: ["action:resume:*", "action:stop-resume"],
  },
  {
    // Ruling 705 addendum 4: Results identifiers are registry-only in P0.
    // Main RES owns activation and every child gate; no claim is made here.
    id: "exp.results.matrix",
    label: "Results grid, matrix and result drawer",
    ships: "hidden",
    experiments: true,
    gate: "Registry routes only in P0; main RES must wire DSX activation and child gates before claiming",
    madeRealBy: "RES",
    options: [
      "cell:*",
      "ex:*",
      "colmenu:*",
      "score:*",
      "banner:*",
      "agg:*",
      "type:*",
      "tts:models",
      "tts:prompts",
      "stt:*",
      "flaky:*",
      "filter:*",
      "sort:*",
      "grid:search",
      "grid:show",
      "grid:diff",
      "grid:review-rows",
      "grid:download",
      "grid:copy-link",
      "grid:edit",
      "grid:add-evaluations",
      "expdrawer:*",
      "drawer:*",
      "rs:*",
      "class:*",
      "chart:*",
      "result:*",
      "view:*",
      "edit:*",
      "record:*",
      "action:rerun:*",
      "action:rerun-toast:*",
      "action:results:*",
      "action:compare:*",
      "action:matrix:*",
      "action:rerun-with-changes:*",
      "info:results-header-interval",
      "url:dataset",
      "state:stopped-at-cost-cap",
      "snip:*",
      "knob:compare:*",
    ],
    entries: {
      routes: [
        {
          re: new RegExp(
            `^/dashboard/develop/experiment/${UUIDISH}/matrix(/|$)`,
          ),
          sample: "/dashboard/develop/experiment/e1/matrix",
          fallback: "/dashboard/develop",
        },
      ],
      search: [
        {
          key: "view",
          values: ["jury"],
          on: ON_EVALS,
        },
      ],
      shortcuts: ["results:*", "drawer:*", "matrix:*"],
    },
  },
  {
    id: "exp.results.paths",
    label: "Agent graph result paths",
    ships: "hidden",
    experiments: true,
    gate: "Registry routes only in P0; main RES must wire ExperimentBar and child mounts before claiming",
    madeRealBy: "RES",
    options: [],
    entries: {
      routes: [
        {
          re: new RegExp(
            `^/dashboard/develop/experiment/${UUIDISH}/paths(/|$)`,
          ),
          sample: "/dashboard/develop/experiment/e1/paths",
          fallback: "/dashboard/develop",
        },
      ],
      shortcuts: ["paths:*"],
    },
  },
  {
    id: "exp.results.summary",
    label: "Experiment summary analysis",
    ships: "hidden",
    experiments: true,
    gate: "Registry routes only in P0; main RES must wire ExperimentBar and child mounts before claiming",
    madeRealBy: "RES",
    options: [],
    entries: {
      shortcuts: ["summary:*"],
    },
  },
  {
    id: "exp.results.traces",
    label: "Experiment run traces",
    ships: "hidden",
    experiments: true,
    gate: "Registry routes only in P0; main RES must wire ExperimentBar and child mounts before claiming",
    madeRealBy: "RES",
    options: ["traces:*"],
    entries: {
      routes: [
        {
          re: new RegExp(
            `^/dashboard/develop/experiment/${UUIDISH}/traces(/|$)`,
          ),
          sample: "/dashboard/develop/experiment/e1/traces",
          fallback: "/dashboard/develop",
        },
      ],
    },
  },
  {
    id: "exp.results.evalVersionImpact",
    label: "Eval-version impact preview and adoption",
    ships: "hidden",
    experiments: true,
    gate: "Registry only in P0; main RES must gate useEvalVersionDoor and ResultGroupHeader before claiming",
    madeRealBy: "RES",
    options: ["ver:*"],
  },
  {
    id: "exp.results.bestOf",
    label: "Best of N from stored scores",
    ships: "hidden",
    experiments: true,
    gate: "Registry only in P0; main RES must gate workbenchModel exploreTabs before claiming",
    madeRealBy: "RES",
    options: ["bestof:*"],
  },
  {
    id: "exp.results.addToDataset",
    label: "Add selected result rows to a dataset",
    ships: "hidden",
    experiments: true,
    gate: "Registry only in P0; main RES must gate SelectionBar action and dialog before claiming",
    madeRealBy: "RES",
    options: [],
  },
  {
    id: "exp.compare",
    label: "Compare runs page and side by side tasks",
    madeRealBy: "RES",
    ships: "hidden",
    experiments: true,
    gate: "routes (launchRoutes.js)",
    options: ["cmp:*", "match:*"],
    entries: {
      routes: [
        {
          re: new RegExp(`^/dashboard/develop/(${UUIDISH})/compare(/|$)`),
          sample: "/dashboard/develop/d1/compare/data",
          fallback: "/dashboard/develop/$1",
        },
      ],
      shortcuts: ["taskCompare:*"],
      verbs: ["row.*.side-by-side"],
    },
  },
  {
    id: "exp.results.iterateDock",
    label: "Iterate dock, quick tests, try a fix",
    ships: "hidden",
    experiments: true,
    gate: "Registry only in P0; main RES must gate all Iterate doors, commands, dock and banners before claiming",
    options: [
      "dock:*",
      "change:*",
      "try:*",
      "action:dock:*",
      "action:try-on-one-row",
      "door:test-a-fix:*",
      "snippet:*",
      "info:dock:*",
      "knob:dock:*",
      "strip:*",
      "data:replay:*",
      "picker:retest-strip",
    ],
    madeRealBy: "RES",
  },
  {
    id: "exp.rerun",
    label: "Re-run with changes dialog (r4 rerun)",
    madeRealBy: "XP",
    ships: "hidden",
    experiments: true,
    gate: "L6 layouts/dashboard (shell mount) + RunSources/RerunWithChangesHost.jsx (its reads)",
    options: [
      "knob:rerun-dialog:*",
      "action:rerun-dialog:*",
      "door:rerun-with-changes:*",
      "info:rerun-dialog:*",
      "state:rerun-dialog:*",
    ],
  },
  {
    id: "exp.leaderboards",
    label: "Leaderboards across datasets",
    madeRealBy: "RES",
    ships: "hidden",
    experiments: true,
    gate: "routes + nav (prototype-only already)",
    options: ["wlb:*", "nav:leaderboards"],
    entries: {
      routes: [
        {
          re: /^\/dashboard\/develop\/leaderboards(\/|$)/,
          sample: "/dashboard/develop/leaderboards",
          fallback: "/dashboard/develop",
        },
      ],
      verbs: ["go.leaderboards"],
    },
  },

  // ------------------------------------------------------------ evals (the interface contract)
  {
    id: "evals.dag",
    label: "Decision tree evals (W7, not inventoried)",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "L6 sections/evals/types/typedViews.js",
    options: [],
    entries: { search: [{ key: "type", values: ["dag"], on: ON_EVALS }] },
  },
  {
    id: "evals.remote",
    label: "Remote scorer evals (W7, not inventoried)",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "L6 sections/evals/types/typedViews.js",
    options: [],
    entries: { search: [{ key: "type", values: ["remote"], on: ON_EVALS }] },
  },
  {
    id: "evals.jury",
    label: "Composite jury seats, step levels, checks and the jury report",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "L6 evals/types/CompositeChildControls.jsx + CompositeJuryReport.jsx",
    options: [],
  },
  {
    id: "evals.agreement",
    label: "Agreement with reviewers",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "L6 sections/evals/alignment/AgreementTab.jsx",
    options: [],
    entries: {
      search: [
        { key: "tab", values: ["agreement"], replace: "details", on: ON_EVALS },
      ],
    },
  },
  {
    id: "evals.agreementTools",
    label: "Compare, improve and train judges with confidence reports",
    madeRealBy: "EVAL",
    ships: "hidden",
    parent: "evals.agreement",
    gate: "sections/evals/alignment/AgreementTab.jsx",
    options: [],
  },
  {
    id: "evals.extras",
    label: "Eval editor and group options that need backend fields",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "hosts in evals (adapter rows; ship hidden)",
    options: [
      "editor:error-localization",
      "editor:prompt",
      "editor:few-shot",
      "editor:output-type",
      "editor:composite-weights",
      "evalcreate:type",
      "evalcreate:save-add",
      "groups:add-a-group",
      "test:editor",
    ],
    why: "Adapter rows inside real Eval pages: their hosts stay (dev's), the inventory flags field gaps; no lane owns them by Tuesday.",
  },
  {
    id: "evals.groups",
    label: "Create an eval group and manage its members on the Groups pages",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "none: sections/evals/Groups (GroupsGrid with NewGroupDialog, IndividualGroup with DeleteEvalFromGroupDialog) renders in every build",
    options: ["groups:create", "groups:detail"],
    why: "Create Groups saves a name, description and chosen evaluations with POST eval-groups/ and opens the group page. The group page reads the group and its members, removes a member with POST eval-groups/edit-eval-list/ and deletes the group when its last member is removed. Every read and write is limited to the caller's organization and workspace. Adding a group to an experiment or dataset (groups:add-a-group) is not part of this surface.",
  },
  {
    id: "evals.createName",
    label: "Say a refused evaluation name at the Name field when creating one",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "none: the Create page (sections/evals/components/EvalCreatePage.jsx) and the picker's Create new (EvalPickerCreateNew.jsx) render in every build; both read createNameError.js",
    options: ["evalcreate:name"],
    why: "Both forms save a draft and publish it with its name through PUT eval-templates/{id}/update/, which applies the create-v2 naming rules: a name another evaluation in the caller's organization or a built-in evaluation uses, or one the format rules refuse, is refused and said at the Name field while the draft stays unpublished. A name used only in another organization is free.",
  },
  {
    id: "evals.createOutputType",
    label:
      "Choose the output type and pass threshold when creating an evaluation",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "none: OutputTypeConfig on the Create page (EvalCreatePage.jsx), and OutputTypeConfig with hideChoices in the picker's Create new (EvalPickerCreateNew.jsx), render in every build",
    options: ["evalcreate:output-type"],
    why: "The Create page offers Pass/fail, Scoring and Choices with a pass threshold and choice mapping where applicable; the picker's Create new offers Pass/fail and Scoring with a pass threshold. Choices is not offered in the picker. Publishing preserves the selected output type and its scoring configuration; the dataset eval list reports the pass rule, and judged cells reflect the evaluation output. Reads and runs stay inside the caller's organization.",
  },
  {
    id: "evals.editorPassThreshold",
    label: "Set the pass threshold of a dataset's saved evaluation",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "none: the evaluation editor (common/EvalPicker/EvalPickerConfigFull.jsx with OutputTypeConfig) renders in every build",
    options: ["editor:pass-threshold"],
    why: "In a dataset's evaluation editor, Update saves the Scoring pass threshold to that saved evaluation's run settings (config.run_config.pass_threshold) with POST edit_and_run_user_eval, keeps the version it runs and saves no new version. The dataset eval list reports it as the pass rule a run applies, and reopening the editor shows it. Reads and writes stay inside the caller's organization and workspace.",
  },
  {
    id: "evals.editorHeader",
    label: "The evaluation editor's header names the version being configured",
    madeRealBy: "EVAL",
    ships: "hidden",
    gate: "none: the header of common/EvalPicker/EvalPickerConfigFull.jsx (VersionBadge) renders in every build",
    options: ["editor:header"],
    why: "The header shows the evaluation's name and type and the saved version being configured: the one chosen in the version list, else the evaluation's current version, from GET eval-templates/{id}/versions/ and detail/. Unsaved changes show as Edited.",
  },
]);

/** Surface by id. */
export const SURFACE_BY_ID = Object.freeze(
  Object.fromEntries(LAUNCH_SURFACES.map((s) => [s.id, s])),
);

/** Glossary keys a launch build keeps (they describe shown grid controls). */
export const LAUNCH_GLOSSARY_KEEP = Object.freeze([
  "wrapLines",
  "matchedByMeaning",
]);

/**
 * Query keys the dataset page reads that open only shown surfaces (the
 * launch-switch test derives every key the page reads from the source and
 * requires each to be here, under a hidden surface's search rule, or read
 * only inside a hidden host).
 */
export const LAUNCH_DATASET_PAGE_KEYS = Object.freeze({
  tab: "DevelopDetailView coerces a hidden tab to Data (isDatasetTabShown)",
  new_experiment: "classic prompt-create drawer (J09 no-hiding)",
  row: "the Row view (dev's row read)",
  rowView: "Row view size (full)",
  edit: "Row view one-shot field editor (L1 classic write)",
  q: "grid search (dev's get-dataset-table search)",
  filters: "grid column filters (dev's filters)",
  density: "grid density (client only)",
  noteDraft: "Annotations tab draft note (dev annotations)",
  datasetId: "AddColumn / EditEvaluation pages of this dataset",
  optimizationId: "Optimization tab (dev's)",
  trialId: "Optimization tab trial (dev's)",
  fromScenario: "dev's scenario flow (DatasetsSelectRow, origin/dev)",
  scenarioId: "dev's scenario flow (DatasetsSelectRow, origin/dev)",
  scenarioName: "dev's scenario flow (DatasetsSelectRow, origin/dev)",
});

/**
 * The Add rows sheet's tiles (importOptions.js ids) and the surface each
 * one needs. A tile not listed here never shows in a launch build.
 */
export const ADD_ROWS_TILE_SURFACE = Object.freeze({
  fromProduction: "dataset.fromProduction",
  uploadFile: "dataset.addRows",
  paste: "dataset.imports.pasteRows",
  addFromExistingDataset: "dataset.addRows",
  importFromHuggingface: "hf.addToExisting",
  generateRows: "dataset.imports.generateRowsTile",
  // The sheet's blank row is a pending change (AddRowDrawer addBlankRow);
  // the palette's Add row is L1's classic write (dataset.addBlankRow).
  "empty-row": "dataset.addRows",
});

/** Keymap sheet sections a launch build lists (others are hidden pages). */
export const LAUNCH_KEYMAP_SURFACES = Object.freeze([
  "global",
  "list",
  "grid",
  "row",
  "review",
]);

const globRe = (glob) =>
  new RegExp(
    `^${glob
      .split("*")
      .map((p) => p.replace(/[.+?^${}()|[\]\\]/g, "\\$&"))
      .join(".*")}$`,
  );

/** Whether an id (option id, verb id) matches an exact id or a `*` glob. */
export function matchesGlob(glob, id) {
  if (glob === id) return true;
  if (!glob.includes("*")) return false;
  return globRe(glob).test(String(id ?? ""));
}

/** The surfaces whose `options` carry an inventory option id. */
export function surfacesOfOption(optionId) {
  return LAUNCH_SURFACES.filter((s) =>
    s.options.some((g) => matchesGlob(g, optionId)),
  );
}

/** Each row tab has one feature owner; Task remains a prototype-only world. */
export const ROW_TAB_SURFACES = Object.freeze({
  runs: "dataset.rowExtras.runs",
  // Comments and the history share the Activity tab (either one shows it).
  activity: "dataset.rowTab.activity",
  conversation: "dataset.rowExtras.conversation",
  trajectory: "dataset.rowExtras.conversation",
  task: "shell.protoOnly",
});

/** Shared by experiment tabs and their route-gate contract tests. */
export const EXPERIMENT_TAB_SURFACES = Object.freeze({
  data: "exp.results.matrix",
  matrix: "exp.results.matrix",
  paths: "exp.results.paths",
  summary: "exp.results.summary",
  traces: "exp.results.traces",
});
