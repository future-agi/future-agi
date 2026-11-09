import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it } from "vitest";
import {
  addRowsTilesIn,
  rowTabShownIn,
  setLaunchOverrideForTest,
} from "../launchSwitch";
import { sanitizeLaunchSearch } from "../launchSearch";
import { commentDeepLink } from "src/sections/common/Collab/commentModel";
import { WIZARD_SETTING_SURFACE, wizardSettingShown } from "../wizardSettings";
import {
  ADD_ROWS_TILE_SURFACE,
  EXPERIMENT_TAB_SURFACES,
  LAUNCH_SURFACES,
  ROW_TAB_SURFACES,
} from "../launchSurfaces";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import { offeredOrganizeItems } from "src/sections/develop/organize/organizeItems";
import { createDoorShown } from "src/sections/develop/importOptions";
import { HELD_749 } from "./registryClaims";
import splits from "../__fixtures__/registry-splits-705.json";
import resultsBase from "../__fixtures__/registry-results-base-705.json";
import {
  literalSurfaceReads,
  resolveWiringSites,
} from "./registryWiringAdditions";
import { resolveCapOwners } from "./registryCapOwners";
import {
  P0_SETTING_OWNERS,
  resolveSettingOwners,
  settingClaimDrift,
  settingMoves,
} from "./registrySettingOwners";

const read = (file) =>
  readFileSync(resolve(process.cwd(), "src", file), "utf8");
const row = "sections/develop-detail/DataTab/DatapointDrawerV2/";
const bar = "sections/develop-detail/DevelopBarRightSection/";
const retired = Object.entries(splits)
  .filter(([id, children]) => !children.includes(id))
  .map(([id]) => id);
const claimed = (id) => ({
  launch: true,
  experimentsBasic: true,
  madeReal: new Set(id ? [id] : []),
});
afterEach(() => setLaunchOverrideForTest(null));

// Each literal read in the migrated files is pinned, including repetitions.
// A misplaced copy/paste gate changes this map, not merely registry coverage.
const sites = [
  [
    "sections/develop-detail/DataTab/DataGrid/gridContextMenu.js",
    [
      "dataset.collab.comments",
      "dataset.collab.translate",
      "dataset.collab.assignments",
      "dataset.splits",
    ],
  ],
  [
    row + "RowFieldsTab.jsx",
    ["dataset.collab.comments", "dataset.collab.translate"],
  ],
  [
    row + "RowViewHeader.jsx",
    ["dataset.splits", "dataset.collab.comments", "dataset.collab.assignments"],
  ],
  [
    row + "RowView.jsx",
    [
      "dataset.rowExtras.runs",
      "dataset.collab.comments",
      "dataset.versions",
      "dataset.rowExtras.activity",
      "exp.new.scope",
    ],
  ],
  [
    row + "RowActivityTab.jsx",
    ["dataset.rowExtras.activity", "dataset.collab.comments"],
  ],
  [row + "RowViewBody.jsx", ["exp.new.scope"]],
  [
    bar + "DevelopBarLeftSection.jsx",
    ["dataset.filtersSystem.pills", "dataset.filtersSystem.expressions"],
  ],
  [
    bar + "DevelopDataRightSection.jsx",
    ["dataset.schema", "dataset.savedViews"],
  ],
  [
    "sections/develop/AddRowDrawer/ExistingDatasetModal.jsx",
    ["dataset.imports.sources"],
  ],
  [
    "sections/common/RunSources/RunOnRowsButton.jsx",
    ["exp.new", "exp.new.scope"],
  ],
  [
    "sections/develop-detail/Experiment/costEstimateModel.js",
    ["exp.new.runSettings"],
  ],
  [
    "sections/common/Collab/RowCollabPills.jsx",
    ["dataset.collab.comments", "dataset.collab.assignments"],
  ],
  [
    "sections/develop/AddDatasetDrawer/uploadBody.js",
    ["dataset.imports.columnRoles", "dataset.imports.transforms"],
  ],
  [
    "sections/develop-detail/AddColumn/rerunScope.js",
    ["dataset.runPromptEdit"],
  ],
  [
    "sections/develop-detail/DevelopBar/DatasetHeaderBar.jsx",
    [
      "dataset.settings",
      "dataset.share",
      "dataset.audit",
      "dataset.fromProduction",
      "dataset.share",
      "dataset.audit",
      "dataset.versions",
      "dataset.imports.sources",
      "dataset.imports.sources",
    ],
  ],
  [
    "sections/develop/importOptions.js",
    ["dataset.fromProduction", "dataset.imports.blankGrid", "hf.create"],
  ],
  [
    "sections/common/EvaluationDrawer/SavedEvalsList.jsx",
    [
      "dataset.evalVersionNotice",
      "dataset.evalScoring",
      "dataset.experimentDefaults",
    ],
  ],
  [
    "sections/develop-detail/Experiment/StepperComponentExperiment/ReviewStep.jsx",
    [
      "exp.new.runSettings",
      "exp.new.runSettings",
      "exp.new.runSettings",
      "exp.new.scope",
      "exp.new.runSettings",
      "exp.new.record",
      "dataset.versions",
    ],
  ],
];

describe("P0 gate consumers", () => {
  it.each(resolveWiringSites(sites))(
    "pins every literal surface read in %s",
    (file, expected) => {
      const source = read(file);
      expect(literalSurfaceReads(source), file).toEqual(expected);
      for (const id of retired) expect(source).not.toContain(`"${id}"`);
    },
  );

  it("uses the correct feature for each wizard setting and import tile", () => {
    // The P0 pins. A settingOwner entry may re-point an owner (resolved
    // below), never relax these.
    expect(P0_SETTING_OWNERS.WIZARD_SETTING_SURFACE).toEqual({
      description: "exp.rename",
      moreSettings: "exp.new.record",
      rowLimits: "exp.new.scope",
      sample: "exp.new.scope",
      theseRows: "exp.new.scope",
      version: "dataset.versions",
      splits: "dataset.splits",
    });
    expect(P0_SETTING_OWNERS.ADD_ROWS_TILE_SURFACE.generateRows).toBe(
      "dataset.imports.generateRowsTile",
    );
    const owners = resolveSettingOwners();
    expect(WIZARD_SETTING_SURFACE).toEqual(owners.WIZARD_SETTING_SURFACE);
    for (const id of new Set(Object.values(WIZARD_SETTING_SURFACE))) {
      setLaunchOverrideForTest(claimed(id));
      for (const [setting, owner] of Object.entries(WIZARD_SETTING_SURFACE))
        expect(wizardSettingShown(setting), setting).toBe(
          owner === id && !HELD_749.has(owner),
        );
    }
    const tiles = new Set([
      "generateRows",
      ...settingMoves(owners)
        .filter(({ map }) => map === "ADD_ROWS_TILE_SURFACE")
        .map(({ key }) => key),
    ]);
    for (const tile of tiles)
      expect(ADD_ROWS_TILE_SURFACE[tile], tile).toBe(
        owners.ADD_ROWS_TILE_SURFACE[tile],
      );
    setLaunchOverrideForTest(claimed("dataset.imports.sources"));
    // DSX J09 / 767/769/770: pre-existing blank grids need no claim.
    expect(createDoorShown("manuallyCreateDataset")).toBe(true);
    setLaunchOverrideForTest(claimed("dataset.imports.blankGrid"));
    expect(createDoorShown("manuallyCreateDataset")).toBe(true);
  });

  it("shows a moved setting or tile only under its new owner's claim", () => {
    // Literal pins, flag OFF and ON, for each settingOwner move: hidden with
    // no claim, shown by the new owner's claim alone, hidden under every
    // other single claim (its P0 owner's included).
    const owners = resolveSettingOwners();
    const ids = new Set([
      ...LAUNCH_SURFACES.map((surface) => surface.id),
      ...Object.values(owners).flatMap((keys) => Object.values(keys)),
    ]);
    const shownUnder = (map, key, state) => {
      setLaunchOverrideForTest(state);
      return map === "WIZARD_SETTING_SURFACE"
        ? wizardSettingShown(key)
        : addRowsTilesIn([{ id: key }], state).length === 1;
    };
    expect(
      settingClaimDrift(settingMoves(owners), [...ids], shownUnder),
    ).toEqual(
      [false, true].flatMap((experimentsBasic) =>
        settingMoves(owners)
          .filter(({ to }) => HELD_749.has(to))
          .map(({ map, key, to }) => ({
            map,
            key,
            claim: to,
            experimentsBasic,
            actual: false,
          })),
      ),
    );
  });

  it("keeps list folder, star, query, metadata and agent claims independent", () => {
    // Literal pins, flag OFF and ON: each cap is hidden with no claim, shown
    // by its owner's claim alone and hidden under every other single claim.
    // A capOwner entry may re-point the owner, never relax these pins.
    const owners = resolveCapOwners();
    const ids = new Set([
      ...LAUNCH_SURFACES.map((surface) => surface.id),
      ...Object.values(owners),
    ]);
    for (const basic of [false, true]) {
      const only = (id) => ({
        launch: true,
        experimentsBasic: basic,
        madeReal: new Set(id ? [id] : []),
      });
      setLaunchOverrideForTest(only(null));
      const unclaimed = datasetCaps(true);
      for (const key of Object.keys(owners))
        expect(unclaimed[key], `unclaimed basic=${basic} ${key}`).toBe(false);
      for (const id of ids) {
        setLaunchOverrideForTest(only(id));
        const caps = datasetCaps(true);
        for (const [key, owner] of Object.entries(owners))
          expect(caps[key], `${id} basic=${basic} ${key}`).toBe(
            id === owner && !HELD_749.has(owner),
          );
      }
    }
    for (const part of ["folders", "star", "query", "metadata", "agents"]) {
      setLaunchOverrideForTest(claimed(`list.organize.${part}`));
      expect(offeredOrganizeItems().map((item) => item.kind)).toEqual(
        ["metadata", "agents"].includes(part) ? [part] : [],
      );
    }
  });

  it("maps row tabs independently and never enables the prototype Task tab", () => {
    expect(ROW_TAB_SURFACES).toEqual({
      runs: "dataset.rowExtras.runs",
      activity: "dataset.rowTab.activity",
      conversation: "dataset.rowExtras.conversation",
      trajectory: "dataset.rowExtras.conversation",
      task: "shell.protoOnly",
    });
    // claim -> the row tabs it shows (Activity: comments or the history).
    const shows = {
      "dataset.rowExtras.runs": ["runs"],
      "dataset.rowExtras.activity": ["activity"],
      "dataset.collab.comments": ["activity"],
      "dataset.rowExtras.conversation": ["conversation", "trajectory"],
    };
    for (const [id, tabs] of Object.entries(shows)) {
      const state = claimed(id);
      for (const tab of Object.keys(ROW_TAB_SURFACES))
        expect(rowTabShownIn(tab, state), `${id} ${tab}`).toBe(
          tabs.includes(tab),
        );
      for (const tab of ["row", "raw", "annotations"])
        expect(rowTabShownIn(tab, state)).toBe(true);
    }
    const source = read(row + "RowView.jsx");
    expect(source).toContain("isRowTabShown(url.rowTab)");
    expect(source).toMatch(/\.filter\(\(t\) => isRowTabShown\(t\.value\)\)/);
  });

  it("reserves separate Results tab identifiers for main's future gates", () => {
    expect(EXPERIMENT_TAB_SURFACES).toEqual({
      data: "exp.results.matrix",
      matrix: "exp.results.matrix",
      paths: "exp.results.paths",
      summary: "exp.results.summary",
      traces: "exp.results.traces",
    });
  });

  it.each(Object.entries(resultsBase.files))(
    "preserves base Results consumer bytes in %s (main owns activation and child gates)",
    (file, digest) => {
      expect(
        `sha256:${createHash("sha256").update(read(file)).digest("hex")}`,
      ).toBe(digest);
    },
  );

  it("lets comments and the activity history each open the Activity tab alone", () => {
    const page = "/dashboard/develop/9f0c2d1e-1111-4a2b-9c3d-000000000001";
    // Every comment door (row pill, grid cell, field menu) goes through
    // commentDeepLink: rowTab=activity&ract=comments&rcs=open&rcompose=…
    const door = commentDeepLink(new URLSearchParams("tab=data"), "r1", 7);
    door.set("ranchor", "field:c1");
    const kept = (ids) => {
      const state = { ...claimed(), madeReal: new Set(ids) };
      const { search } = sanitizeLaunchSearch(`?${door}`, page, state);
      return [...new URLSearchParams(search).keys()];
    };
    expect(kept([])).toEqual(["tab", "row"]);
    expect(kept(["dataset.collab.comments"])).toEqual([
      "tab",
      "row",
      "rowTab",
      "rcs",
      "rcompose",
      "ranchor",
    ]);
    expect(kept(["dataset.rowExtras.activity"])).toEqual([
      "tab",
      "row",
      "rowTab",
      "ract",
    ]);
    expect(
      kept(["dataset.collab.comments", "dataset.rowExtras.activity"]),
    ).toEqual(["tab", "row", "rowTab", "ract", "rcs", "rcompose", "ranchor"]);
    // RowActivityTab gates its parts (history read by the history claim,
    // threads/composer by comments) and RowCollabPills keeps the hooks'
    // own guards: a hidden part only turns its read off.
    const tab = read(row + "RowActivityTab.jsx");
    expect(tab).toMatch(
      /useRowActivity\(\s*datasetId,\s*rowId,\s*historyShown \? \{\} : \{ enabled: false \},?\s*\)/,
    );
    expect(tab).toMatch(
      /commentsShown && active !== "comments" && rowId && \(\s*<PinnedComposer/,
    );
    const pills = read("sections/common/Collab/RowCollabPills.jsx");
    expect(pills).toContain("...(commentsShown ? {} : { enabled: false })");
    expect(pills).toContain("assignmentsShown ? {} : { enabled: false }");
    expect(pills).not.toMatch(/enabled: (commentsShown|assignmentsShown)\b/);
  });

  it("pins the mixed review settings to scope, runSettings and record independently", () => {
    const source = read(
      "sections/develop-detail/Experiment/StepperComponentExperiment/ReviewStep.jsx",
    );
    for (const [variable, part] of [
      ["scopeShown", "scope"],
      ["runSettingsShown", "runSettings"],
      ["recordShown", "record"],
    ])
      expect(source).toContain(
        `const ${variable} = isSurfaceShown("exp.new.${part}")`,
      );
    expect(source).toMatch(
      /!isEditing && recordShown \?\s*\(?\s*<RecordToggle/,
    );
    expect(source).toContain(
      'const policyShown = scopeShown || values.errorPolicy === "exclude"',
    );
    expect(source).toMatch(/policyShown &&\s*\(?\s*<ErrorPolicySetting/);
    expect(source).not.toContain("exp.new.unhonoured");
  });
});
