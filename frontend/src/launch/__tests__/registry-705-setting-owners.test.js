import { afterEach, describe, expect, it, vi } from "vitest";
import * as registry from "../launchSurfaces";
import { resolveCapOwners } from "./registryCapOwners";
import { moduleWithLaneAdditions } from "./registryLaneAdditionSets";
import {
  P0_SETTING_OWNERS,
  resolveSettingOwners,
  settingClaimDrift,
  settingMoves,
} from "./registrySettingOwners";
import { resolveWiringSites } from "./registryWiringAdditions";

// The F50 split chips (T-GRIDB verify): the wizard's `splits` setting and
// the split options leave dataset.splits for a hidden XP surface of their
// own, so the GRID claim no longer shows a setting the experiment backend
// cannot run. Fixture-only: moduleWithLaneAdditions drops a lane's real
// addition of the same id before injecting this one.
const SPLIT_SCOPE = Object.freeze({
  id: "exp.new.splitScope",
  slot: "XP",
  ships: "hidden",
  splitFrom: "dataset.splits",
  options: Object.freeze([
    "data:rows:split",
    "estimate:smoke-split-suggestion",
    "entry:run-on-rows:training-split",
  ]),
});
const WIZARD = "WIZARD_SETTING_SURFACE";
const TILES = "ADD_ROWS_TILE_SURFACE";
const declaration = (overrides = {}) => ({ ...SPLIT_SCOPE, ...overrides });
/** The registry with `declared` injected (`edit` changes its metadata). */
const shipped = (declared = declaration(), edit = () => {}) =>
  moduleWithLaneAdditions(registry, [declared], (surfaces) =>
    edit(surfaces.find((s) => s.id === declared.id)),
  ).LAUNCH_SURFACES;
const SURFACES = shipped();
const P0_ONLY = moduleWithLaneAdditions(registry, []).LAUNCH_SURFACES;
const entry = (overrides = {}) => ({
  kind: "settingOwner",
  map: WIZARD,
  key: "splits",
  from: "dataset.splits",
  to: SPLIT_SCOPE.id,
  lane: "T-XPC",
  feature: "F05",
  ...overrides,
});
const tile = (overrides = {}) =>
  entry({
    map: TILES,
    key: "generateRows",
    from: "dataset.imports.generateRowsTile",
    to: "dataset.imports.sources",
    lane: "T-INB",
    feature: "F36",
    ...overrides,
  });
const resolve = (
  entries,
  laneDeclarations = [declaration()],
  surfaces = SURFACES,
) => resolveSettingOwners(entries, { laneDeclarations, surfaces });
const p0Maps = () =>
  Object.fromEntries(
    Object.entries(P0_SETTING_OWNERS).map(([map, keys]) => [map, { ...keys }]),
  );
const capEntry = (to) => ({
  kind: "capOwner",
  cap: "listSavedViews",
  from: "list.organize.query",
  to,
  lane: "T-ORG",
  feature: "F43",
});
// Targets a claim does not gate, as capOwner rejects them.
const ungatedTargets = [
  ["always-shown shell.real", "shell.real", "shown-target"],
  ["always-shown launch.fixes", "launch.fixes", "shown-target"],
  // All-on flip: exp.new ships shown behind the flag, an ungated target.
  ["shown-behind-flag exp.new", "exp.new", "shown-target"],
  ["experimentsBasic exp.page", "exp.page", "flag-target"],
  [
    "shared host dataset.rowTab.activity",
    "dataset.rowTab.activity",
    "shared-host-target",
  ],
  ["experiments child exp.rename", "exp.rename", "flag-target"],
  ["experiments child exp.new.scope", "exp.new.scope", "flag-target"],
  ["experiments-only exp.rerun", "exp.rerun", "flag-target"],
  ["experiments-only palette.runs", "palette.runs", "flag-target"],
  ["legacy L1 claim list.real", "list.real", "slot-target"],
  ["L6 shell.protoOnly", "shell.protoOnly", "slot-target"],
];
const invalid = [
  ["unknown map", [entry({ map: "WIZARD_SETTINGS" })], "unknown-map"],
  ["a map no entry moves", [entry({ map: "ROW_TAB_SURFACES" })], "unknown-map"],
  ["prototype map", [entry({ map: "toString" })], "unknown-map"],
  ["missing map", [entry({ map: undefined })], "unknown-map"],
  ["unknown key", [entry({ key: "split" })], "unknown-key"],
  ["prototype key", [entry({ key: "hasOwnProperty" })], "unknown-key"],
  ["the other map's key", [entry({ key: "generateRows" })], "unknown-key"],
  ["non-string key", [entry({ key: ["splits"] })], "unknown-key"],
  ["wrong P0 owner", [entry({ from: "exp.new.scope" })], "wrong-from"],
  ["missing owner", [entry({ from: undefined })], "wrong-from"],
  ["the new owner as from", [entry({ from: SPLIT_SCOPE.id })], "wrong-from"],
  ["wrong tile owner", [tile({ from: "dataset.addRows" })], "wrong-from"],
  ...ungatedTargets.map(([name, to, code]) => [name, [entry({ to })], code]),
  ["unknown target", [entry({ to: "exp.new.typo" })], "unknown-id"],
  ["retired target", [entry({ to: "dataset.collab" })], "retired-id"],
  ["Results child", [entry({ to: "exp.results.matrix" })], "results-id"],
  ["blank target", [entry({ to: " " })], "invalid-to"],
  ["non-string target", [entry({ to: null })], "invalid-to"],
  ["missing lane", [entry({ lane: undefined })], "invalid-lane"],
  ["blank feature", [entry({ feature: " " })], "invalid-feature"],
  ["unknown field", [entry({ active: true })], "unknown-field"],
  ["wire fields on a setting", [entry({ file: "Gate.jsx" })], "unknown-field"],
  ["cap fields on a setting", [entry({ cap: "listStars" })], "unknown-field"],
  ["two entries for one key", [entry(), entry()], "duplicate-setting"],
  [
    "a second active owner",
    [entry(), entry({ lane: "T-GRIDB", to: "dataset.versions" })],
    "duplicate-setting",
  ],
  ["two entries for one tile", [tile(), tile()], "duplicate-setting"],
];
// Lane additions take the same rule, from their declaration and their
// registry metadata alike: [name, declaration, surface edit, code].
const ungatedLane = [
  [
    "a shared-host surface",
    declaration(),
    (s) => Object.assign(s, { anyOf: ["dataset.splits"] }),
    "shared-host-target",
  ],
  [
    "a shared-host declaration",
    declaration({ anyOf: ["dataset.splits"] }),
    undefined,
    "shared-host-target",
  ],
  [
    "a shown declaration",
    declaration({ ships: "shown" }),
    (s) => Object.assign(s, { ships: "hidden" }),
    "shown-target",
  ],
  [
    "a shown surface",
    declaration(),
    (s) => Object.assign(s, { ships: "shown" }),
    "shown-target",
  ],
  [
    "an experimentsBasic surface",
    declaration(),
    (s) => Object.assign(s, { ships: "experimentsBasic" }),
    "flag-target",
  ],
  [
    "an experiments declaration",
    declaration({ experiments: true }),
    undefined,
    "flag-target",
  ],
  [
    "a child of the wizard",
    declaration({ parent: "exp.new" }),
    undefined,
    "parent-target",
  ],
  ["a legacy slot", declaration({ slot: "L6" }), undefined, "slot-target"],
  [
    "a declaration in another slot",
    declaration({ slot: "GRID" }),
    (s) => Object.assign(s, { madeRealBy: "XP" }),
    "slot-target",
  ],
];

afterEach(() => {
  vi.doUnmock("../launchSurfaces");
  vi.resetModules();
});

describe("P0 wizard setting and Add rows tile ownership declarations", () => {
  it("pins both P0 maps and keeps them when no move is declared", () => {
    expect(P0_SETTING_OWNERS).toEqual({
      WIZARD_SETTING_SURFACE: {
        description: "exp.rename",
        moreSettings: "exp.new.record",
        rowLimits: "exp.new.scope",
        sample: "exp.new.scope",
        theseRows: "exp.new.scope",
        version: "dataset.versions",
        splits: "dataset.splits",
      },
      ADD_ROWS_TILE_SURFACE: {
        fromProduction: "dataset.fromProduction",
        uploadFile: "dataset.addRows",
        paste: "dataset.addRows",
        addFromExistingDataset: "dataset.addRows",
        importFromHuggingface: "hf.addToExisting",
        generateRows: "dataset.imports.generateRowsTile",
        "empty-row": "dataset.addRows",
      },
    });
    expect(resolve([])).toEqual(p0Maps());
    expect(settingMoves(resolve([]))).toEqual([]);
  });

  it("moves only the declared setting and never mutates P0", () => {
    const owners = resolve([entry()]);
    const expected = p0Maps();
    expected[WIZARD].splits = SPLIT_SCOPE.id;
    expect(owners).toEqual(expected);
    expect(settingMoves(owners)).toEqual([
      { map: WIZARD, key: "splits", to: SPLIT_SCOPE.id },
    ]);
    expect(P0_SETTING_OWNERS[WIZARD].splits).toBe("dataset.splits");
    expect(resolve([])[WIZARD].splits).toBe("dataset.splits");
  });

  it("moves an Add rows tile to a P0 surface without a lane declaration", () => {
    const owners = resolve([tile()], []);
    expect(owners[TILES].generateRows).toBe("dataset.imports.sources");
    expect(settingMoves(owners)).toEqual([
      { map: TILES, key: "generateRows", to: "dataset.imports.sources" },
    ]);
  });

  it("allows independent moves of distinct settings", () => {
    const owners = resolve([
      entry(),
      entry({ key: "sample", from: "exp.new.scope" }),
      tile(),
    ]);
    expect(settingMoves(owners)).toEqual([
      { map: WIZARD, key: "sample", to: SPLIT_SCOPE.id },
      { map: WIZARD, key: "splits", to: SPLIT_SCOPE.id },
      { map: TILES, key: "generateRows", to: "dataset.imports.sources" },
    ]);
  });

  it("requires new targets to be declared and registered", () => {
    expect(() => resolve([entry()], [])).toThrow("unknown-id");
    expect(() => resolve([entry()], [declaration()], P0_ONLY)).toThrow(
      "missing-surface",
    );
  });

  it.each(ungatedTargets)(
    "takes the capOwner target rule for %s",
    (_name, to, code) => {
      const options = { laneDeclarations: [], surfaces: SURFACES };
      expect(() => resolveCapOwners([capEntry(to)], options)).toThrow(code);
      expect(() => resolveSettingOwners([entry({ to })], options)).toThrow(
        `Setting owner ${code}: ${WIZARD}.splits`,
      );
    },
  );

  it("coexists with capability and literal-site entries without changing them", () => {
    const sites = [["Gate.jsx", ["dataset.schema"]]];
    const wire = {
      file: "Gate.jsx",
      lane: "T-GRID",
      feature: "F47",
      expected: ["dataset.schema", "dataset.savedViews"],
    };
    const cap = capEntry("dataset.savedViews");
    for (const entries of [
      [entry(), wire, cap],
      [cap, wire, entry()],
    ]) {
      expect(
        resolveWiringSites(sites, entries, {
          laneDeclarations: [declaration()],
          surfaces: SURFACES,
        }),
      ).toEqual([[wire.file, wire.expected]]);
      expect(settingMoves(resolve(entries))).toEqual([
        { map: WIZARD, key: "splits", to: SPLIT_SCOPE.id },
      ]);
      expect(
        resolveCapOwners(entries, {
          laneDeclarations: [declaration()],
          surfaces: SURFACES,
        }).listSavedViews,
      ).toBe("dataset.savedViews");
    }
  });

  it("rejects a malformed list", () => {
    expect(() => resolve(null)).toThrow("Setting owner not-list");
  });

  it.each(invalid)("rejects %s", (_name, entries, code) => {
    expect(() => resolve(entries)).toThrow(code);
    // Site consumers validate setting entries too, never silently skip them.
    expect(() =>
      resolveWiringSites([], entries, {
        laneDeclarations: [declaration()],
        surfaces: SURFACES,
      }),
    ).toThrow(code);
  });

  it.each(ungatedLane)(
    "rejects a lane addition with %s",
    (_name, declared, edit, code) => {
      const surfaces = shipped(declared, edit);
      expect(() => resolve([entry()], [declared], surfaces)).toThrow(code);
      expect(() =>
        resolveWiringSites([], [entry()], {
          laneDeclarations: [declared],
          surfaces,
        }),
      ).toThrow(code);
    },
  );
});

describe("a moved setting shows by its new owner's claim alone", () => {
  const MOVES = [{ map: WIZARD, key: "splits", to: SPLIT_SCOPE.id }];
  const IDS = [...SURFACES.map((s) => s.id)];
  const claimGated = (_map, _key, state) => state.madeReal.has(SPLIT_SCOPE.id);

  it("passes a consumer that reads only the new owner, through the launch switch", async () => {
    vi.resetModules();
    vi.doMock("../launchSurfaces", async (original) =>
      moduleWithLaneAdditions(await original(), [declaration()]),
    );
    const { surfaceShownIn } = await import("../launchSwitch");
    const owners = resolve([entry()]);
    const moves = settingMoves(owners);
    expect(moves).toEqual(MOVES);
    // All-on F50: exp.new.splitScope is out of the 749 hold layer, so its
    // claim alone shows the moved setting through the live switch.
    expect(
      settingClaimDrift(moves, IDS, (map, key, state) =>
        surfaceShownIn(owners[map][key], state),
      ),
    ).toEqual([]);
    expect(settingClaimDrift(MOVES, IDS, claimGated)).toEqual([]);
  });

  it("reports the setting visible under another claim", () => {
    const leak = (map, key, state) =>
      claimGated(map, key, state) || state.madeReal.has("dataset.splits");
    expect(settingClaimDrift(MOVES, IDS, leak)).toEqual(
      [false, true].map((experimentsBasic) => ({
        map: WIZARD,
        key: "splits",
        claim: "dataset.splits",
        experimentsBasic,
        actual: true,
      })),
    );
  });

  it("reports the setting shown with no claim", () => {
    const drift = settingClaimDrift(MOVES, IDS, () => true);
    expect(drift.filter((d) => d.claim === null)).toHaveLength(2);
    expect(drift).toHaveLength(2 * IDS.length);
  });

  it("reports the setting its owner's claim shows only with experimentsBasic", () => {
    const flagged = (map, key, state) =>
      claimGated(map, key, state) && state.experimentsBasic;
    expect(settingClaimDrift(MOVES, IDS, flagged)).toEqual([
      {
        map: WIZARD,
        key: "splits",
        claim: SPLIT_SCOPE.id,
        experimentsBasic: false,
        actual: false,
      },
    ]);
  });
});
