import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as registry from "../launchSurfaces";
import prototypePaths from "../__fixtures__/registry-prototype-paths-705.json";
import { resolveCapOwners } from "./registryCapOwners";
import {
  flowsWithWalks,
  moduleWithClaims,
  sourceWithClaims,
} from "./registryClaimSets";
import {
  RUN_CONTROL,
  moduleWithLaneAdditions,
} from "./registryLaneAdditionSets";
import { P0_PATH_OWNERS, resolvePathOwners } from "./registryPathOwners";
import { resolveSettingOwners } from "./registrySettingOwners";
import { resolveWiringSites } from "./registryWiringAdditions";

// Main ruling (10-28): the F06 resume endpoint is run control, not rerun.
// T-XPB's exp.runControl (RUN_CONTROL, a fixture stand-in for its lane
// addition) is the single owner; exp.rerun keeps every other pinned path.
const RESUME = "/model-hub/experiments/v2/{experiment_id}/resume/";
const RERUN = "/model-hub/r4/runs/{run_id}/rerun/";
const ITERATE = "/model-hub/experiments/v2/{experiment_id}/runs/";
const SOURCE = readFileSync(
  resolvePath(process.cwd(), "src/launch/madeReal.js"),
  "utf8",
);
const FLOWS = readFileSync(
  resolvePath(process.cwd(), "../e2e/FLOWS.md"),
  "utf8",
);
const WALK = "FIXTURE-E2E-006: two-organization run control.";
/** The registry with `declared` injected (`edit` changes its metadata). */
const shipped = (declared = RUN_CONTROL, edit = () => {}) =>
  moduleWithLaneAdditions(registry, [declared], (surfaces) =>
    edit(surfaces.find((s) => s.id === declared.id)),
  ).LAUNCH_SURFACES;
const SURFACES = shipped();
const P0_ONLY = moduleWithLaneAdditions(registry, []).LAUNCH_SURFACES;
const entry = (overrides = {}) => ({
  kind: "pathOwner",
  path: RESUME,
  from: "exp.rerun",
  to: RUN_CONTROL.id,
  lane: "T-XPB",
  feature: "F06",
  ...overrides,
});
const resolve = (
  entries,
  laneDeclarations = [RUN_CONTROL],
  surfaces = SURFACES,
) => resolvePathOwners(entries, { laneDeclarations, surfaces });
const pathsOf = (owners, id) =>
  Object.keys(owners)
    .filter((path) => owners[path].includes(id))
    .sort();
const invalid = [
  ["unknown path", [entry({ path: "/model-hub/not-a-path/" })], "unknown-path"],
  [
    "contracted path never in P0",
    [entry({ path: "/model-hub/develops/get-datasets/" })],
    "unknown-path",
  ],
  ["prototype path", [entry({ path: "toString" })], "unknown-path"],
  ["non-string path", [entry({ path: [RESUME] })], "unknown-path"],
  ["a chained re-pin", [entry({ from: RUN_CONTROL.id })], "wrong-from"],
  [
    "another path's owner",
    [entry({ from: "exp.results.matrix" })],
    "wrong-from",
  ],
  ["missing owner", [entry({ from: undefined })], "wrong-from"],
  // Targets a claim does not gate.
  ["always-shown shell.real", [entry({ to: "shell.real" })], "shown-target"],
  // All-on flip: exp.new ships shown behind the flag, an ungated target.
  ["shown-behind-flag exp.new", [entry({ to: "exp.new" })], "shown-target"],
  ["experimentsBasic exp.page", [entry({ to: "exp.page" })], "flag-target"],
  [
    "shared host dataset.rowTab.activity",
    [entry({ to: "dataset.rowTab.activity" })],
    "shared-host-target",
  ],
  [
    "wizard child exp.new.scope",
    [entry({ to: "exp.new.scope" })],
    "parent-target",
  ],
  ["wizard child exp.rename", [entry({ to: "exp.rename" })], "parent-target"],
  ["L6 shell.protoOnly", [entry({ to: "shell.protoOnly" })], "slot-target"],
  ["legacy L1 claim list.real", [entry({ to: "list.real" })], "slot-target"],
  ["unknown target", [entry({ to: "exp.runControl.typo" })], "unknown-id"],
  ["retired target", [entry({ to: "exp.results" })], "retired-id"],
  ["Results child", [entry({ to: "exp.results.matrix" })], "results-id"],
  ["blank target", [entry({ to: " " })], "invalid-to"],
  // Claim-gated, but another slot's: the path stays in its P0 slot.
  ["a GRID surface", [entry({ to: "dataset.splits" })], "cross-slot"],
  ["a RES surface", [entry({ to: "palette.runs" })], "cross-slot"],
  [
    "a Results path moved into XP",
    [entry({ path: ITERATE, from: "exp.results.iterateDock" })],
    "cross-slot",
  ],
  ["two entries for one path", [entry(), entry()], "duplicate-path"],
  [
    "a second XP lane's entry",
    [entry(), entry({ lane: "T-XPC", feature: "F05" })],
    "duplicate-path",
  ],
  [
    "a second XP lane's other target",
    [entry(), entry({ lane: "T-XPC", feature: "F05", to: "exp.rerun" })],
    "duplicate-path",
  ],
  ["missing lane", [entry({ lane: undefined })], "invalid-lane"],
  ["blank feature", [entry({ feature: " " })], "invalid-feature"],
  ["unknown field", [entry({ active: true })], "unknown-field"],
  ["cap fields on a path", [entry({ cap: "listStars" })], "unknown-field"],
  ["setting fields on a path", [entry({ key: "splits" })], "unknown-field"],
];
// [name, declaration, surface edit, code]
const ungatedLane = [
  [
    "a shared-host surface",
    RUN_CONTROL,
    (s) => Object.assign(s, { anyOf: ["exp.rerun"] }),
    "shared-host-target",
  ],
  [
    "a shared-host declaration",
    { ...RUN_CONTROL, anyOf: ["exp.rerun"] },
    undefined,
    "shared-host-target",
  ],
  [
    "a shown declaration",
    { ...RUN_CONTROL, ships: "shown" },
    (s) => Object.assign(s, { ships: "hidden" }),
    "shown-target",
  ],
  [
    "an experimentsBasic surface",
    RUN_CONTROL,
    (s) => Object.assign(s, { ships: "experimentsBasic" }),
    "flag-target",
  ],
  [
    "a child of the experiment page",
    { ...RUN_CONTROL, parent: "exp.page" },
    undefined,
    "parent-target",
  ],
  ["a legacy slot", { ...RUN_CONTROL, slot: "L6" }, undefined, "slot-target"],
  [
    "a declaration in another slot",
    { ...RUN_CONTROL, slot: "GRID" },
    (s) => Object.assign(s, { madeRealBy: "XP" }),
    "slot-target",
  ],
  [
    "an addition in another slot",
    { ...RUN_CONTROL, slot: "GRID" },
    undefined,
    "cross-slot",
  ],
];

/** claimViolations through the fixture, as registry-705.test.js reads it. */
async function violationsWith(wiring, set) {
  vi.resetModules();
  vi.doMock("../__fixtures__/registry-lane-additions-705.json", () => ({
    default: [RUN_CONTROL],
  }));
  vi.doMock("../__fixtures__/registry-wiring-additions-705.json", () => ({
    default: wiring,
  }));
  vi.doMock("../launchSurfaces", async (original) =>
    moduleWithLaneAdditions(await original(), [RUN_CONTROL]),
  );
  vi.doMock("../madeReal", async (original) =>
    moduleWithClaims(await original(), set),
  );
  const contract = await import("./registryClaims");
  const madeReal = await import("../madeReal");
  return () =>
    contract
      .claimViolations({
        MADE_REAL: madeReal.MADE_REAL,
        MADE_REAL_PATHS: madeReal.MADE_REAL_PATHS,
        source: sourceWithClaims(SOURCE, set),
        flows: flowsWithWalks(FLOWS, set),
        live: madeReal.madeRealSurfaceIds(),
      })
      .map(({ code, slot, value }) => [code, slot, value]);
}
const setOf = (claims, paths, slot = "XP") => ({
  claims: { [slot]: claims.map((id) => ({ id, comment: WALK })) },
  paths: { [slot]: paths.map((id) => ({ id, comment: WALK })) },
  flows: ["### FIXTURE-E2E-006 - fixture only"],
});

afterEach(() => {
  vi.doUnmock("../__fixtures__/registry-lane-additions-705.json");
  vi.doUnmock("../__fixtures__/registry-wiring-additions-705.json");
  vi.doUnmock("../launchSurfaces");
  vi.doUnmock("../madeReal");
  vi.resetModules();
});

describe("P0 prototype path ownership declarations", () => {
  it("keeps the pinned owners when no move is declared", () => {
    expect(P0_PATH_OWNERS).toEqual(prototypePaths.owners);
    expect(resolve([])).toEqual(prototypePaths.owners);
    expect(pathsOf(P0_PATH_OWNERS, "exp.rerun")).toEqual([RESUME, RERUN]);
  });

  it("moves resume to run control alone and never mutates P0", () => {
    const owners = resolve([entry()]);
    expect(owners[RESUME]).toEqual([RUN_CONTROL.id]);
    expect(P0_PATH_OWNERS[RESUME]).toEqual(["exp.rerun"]);
    expect(prototypePaths.owners[RESUME]).toEqual(["exp.rerun"]);
    expect(resolve([])[RESUME]).toEqual(["exp.rerun"]);
  });

  it("leaves exp.rerun every other pinned path, and every other path its owners", () => {
    const owners = resolve([entry()]);
    expect(Object.keys(owners)).toEqual(Object.keys(prototypePaths.owners));
    for (const [path, ids] of Object.entries(prototypePaths.owners))
      if (path !== RESUME) expect(owners[path], path).toEqual(ids);
    expect(pathsOf(owners, "exp.rerun")).toEqual(
      pathsOf(prototypePaths.owners, "exp.rerun").filter((p) => p !== RESUME),
    );
    expect(pathsOf(owners, "exp.rerun")).toEqual([RERUN]);
    expect(pathsOf(owners, RUN_CONTROL.id)).toEqual([RESUME]);
  });

  it("admits an experiments surface for a path, never for a capability or setting", () => {
    expect(RUN_CONTROL.experiments).toBe(true);
    const options = { laneDeclarations: [RUN_CONTROL], surfaces: SURFACES };
    expect(resolvePathOwners([entry()], options)[RESUME]).toEqual([
      RUN_CONTROL.id,
    ]);
    expect(() =>
      resolveCapOwners(
        [
          {
            kind: "capOwner",
            cap: "listSavedViews",
            from: "list.organize.query",
            to: RUN_CONTROL.id,
            lane: "T-XPB",
            feature: "F06",
          },
        ],
        options,
      ),
    ).toThrow("Cap owner flag-target");
    expect(() =>
      resolveSettingOwners(
        [
          {
            kind: "settingOwner",
            map: "WIZARD_SETTING_SURFACE",
            key: "splits",
            from: "dataset.splits",
            to: RUN_CONTROL.id,
            lane: "T-XPB",
            feature: "F06",
          },
        ],
        options,
      ),
    ).toThrow("Setting owner flag-target");
  });

  it("accepts a P0 surface of the same slot without a lane declaration", () => {
    expect(resolve([entry({ to: "exp.rerun" })], [], P0_ONLY)[RESUME]).toEqual([
      "exp.rerun",
    ]);
  });

  it("requires new targets to be declared and registered", () => {
    expect(() => resolve([entry()], [])).toThrow("unknown-id");
    expect(() => resolve([entry()], [RUN_CONTROL], P0_ONLY)).toThrow(
      "missing-surface",
    );
  });

  it("rejects a malformed list", () => {
    expect(() => resolve(null)).toThrow("Path owner not-list");
  });

  it.each(invalid)("rejects %s", (_name, entries, code) => {
    expect(() => resolve(entries)).toThrow(code);
    // Site consumers validate path entries too, never silently skip them.
    expect(() =>
      resolveWiringSites([], entries, {
        laneDeclarations: [RUN_CONTROL],
        surfaces: SURFACES,
      }),
    ).toThrow(code);
  });

  it.each(ungatedLane)(
    "rejects a lane addition with %s",
    (_name, declared, edit, code) => {
      const surfaces = shipped(declared, edit);
      expect(() => resolve([entry()], [declared], surfaces)).toThrow(code);
    },
  );
});

describe("P0 claim contract reads the resolved path owners", () => {
  const RUN = RUN_CONTROL.id;

  it("rejects resume under the run-control claim without the move", async () => {
    const violations = await violationsWith([], setOf([RUN], [RESUME]));
    expect(violations()).toEqual([["path-without-surface", "XP", RESUME]]);
  });

  it("accepts resume under the run-control claim with the move", async () => {
    const violations = await violationsWith([entry()], setOf([RUN], [RESUME]));
    expect(violations()).toEqual([]);
  });

  it("no longer backs resume with an exp.rerun claim", async () => {
    const violations = await violationsWith(
      [entry()],
      setOf(["exp.rerun"], [RESUME]),
    );
    expect(violations()).toEqual([["path-without-surface", "XP", RESUME]]);
  });

  it("still backs the rerun path with exp.rerun, never with run control", async () => {
    const rerun = await violationsWith(
      [entry()],
      setOf(["exp.rerun"], [RERUN]),
    );
    expect(rerun()).toEqual([]);
    const run = await violationsWith([entry()], setOf([RUN], [RERUN]));
    expect(run()).toEqual([["path-without-surface", "XP", RERUN]]);
  });

  it("allows one claim of resume in the XP slot", async () => {
    const violations = await violationsWith(
      [entry()],
      setOf([RUN], [RESUME, RESUME]),
    );
    expect(violations()).toEqual([["duplicate-path", "XP", RESUME]]);
  });

  it("does not let another slot claim the moved path", async () => {
    const violations = await violationsWith(
      [entry()],
      setOf(["dataset.splits"], [RESUME], "GRID"),
    );
    expect(violations()).toEqual([
      ["wrong-path-slot", "GRID", RESUME],
      ["path-without-surface", "GRID", RESUME],
    ]);
  });

  it("stops on an invalid move instead of ignoring it", async () => {
    const violations = await violationsWith(
      [entry({ from: "exp.results.matrix" })],
      setOf([RUN], [RESUME]),
    );
    expect(violations).toThrow(`Path owner wrong-from: ${RESUME}`);
  });
});
