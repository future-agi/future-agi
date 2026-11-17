import { describe, expect, it } from "vitest";
import { LAUNCH_SURFACES } from "../launchSurfaces";
import { P0_CAP_OWNERS, resolveCapOwners } from "./registryCapOwners";
import { resolveWiringSites } from "./registryWiringAdditions";

// A fixture-only lane addition (shaped like T-ORG's list.organize.savedViews,
// under an id no lane registers, so a lane registry never shadows it): its
// declaration (registry-lane-additions-705.json) and the surface beside it.
const TARGET = "list.organize.capOwnerFixture";
const declaration = (overrides = {}) => ({
  id: TARGET,
  slot: "ORG",
  ships: "hidden",
  splitFrom: "list.organize.query",
  options: [],
  ...overrides,
});
const surface = (overrides = {}) => ({
  id: TARGET,
  label: "Fixture list capability",
  ships: "hidden",
  gate: "develop/realBackendCaps.js listSavedViews",
  options: [],
  madeRealBy: "ORG",
  ...overrides,
});
const declarations = [declaration()];
const SURFACES = [...LAUNCH_SURFACES, surface()];
const entry = (overrides = {}) => ({
  kind: "capOwner",
  cap: "listSavedViews",
  from: "list.organize.query",
  to: TARGET,
  lane: "T-ORG",
  feature: "F43",
  ...overrides,
});
const resolve = (
  entries,
  laneDeclarations = declarations,
  surfaces = SURFACES,
) => resolveCapOwners(entries, { laneDeclarations, surfaces });
const invalid = [
  ["unknown cap", [entry({ cap: "listOther" })], "unknown-cap"],
  ["unowned datasetCaps key", [entry({ cap: "listQuery" })], "unknown-cap"],
  ["prototype key", [entry({ cap: "toString" })], "unknown-cap"],
  ["non-string cap", [entry({ cap: ["listSavedViews"] })], "unknown-cap"],
  ["wrong P0 owner", [entry({ from: "list.organize.star" })], "wrong-from"],
  ["missing owner", [entry({ from: undefined })], "wrong-from"],
  ["unknown target", [entry({ to: "list.organize.typo" })], "unknown-id"],
  ["retired target", [entry({ to: "dataset.collab" })], "retired-id"],
  ["retired Results parent", [entry({ to: "exp.results" })], "retired-id"],
  ["Results child", [entry({ to: "exp.results.matrix" })], "results-id"],
  ["future Results child", [entry({ to: "exp.results.new" })], "results-id"],
  ["blank target", [entry({ to: " " })], "invalid-to"],
  ["non-string target", [entry({ to: null })], "invalid-to"],
  ["missing lane", [entry({ lane: undefined })], "invalid-lane"],
  ["blank feature", [entry({ feature: " " })], "invalid-feature"],
  ["unknown field", [entry({ active: true })], "unknown-field"],
  ["wire fields on a cap", [entry({ file: "Gate.jsx" })], "unknown-field"],
  ["duplicate entry", [entry(), entry()], "duplicate-cap"],
  [
    "second active owner",
    [entry(), entry({ lane: "T-GRID", to: "dataset.savedViews" })],
    "duplicate-cap",
  ],
  // Targets a claim does not gate: each would show the cap with no claim,
  // with the experimentsBasic flag, or through another surface's claim.
  ["always-shown shell.real", [entry({ to: "shell.real" })], "shown-target"],
  [
    "always-shown launch.fixes",
    [entry({ to: "launch.fixes" })],
    "shown-target",
  ],
  // All-on flip: exp.new ships shown behind the flag, an ungated target.
  ["shown-behind-flag exp.new", [entry({ to: "exp.new" })], "shown-target"],
  ["experimentsBasic exp.page", [entry({ to: "exp.page" })], "flag-target"],
  [
    "shared host dataset.rowTab.activity",
    [entry({ to: "dataset.rowTab.activity" })],
    "shared-host-target",
  ],
  [
    "experiments child exp.rename",
    [entry({ to: "exp.rename" })],
    "flag-target",
  ],
  [
    "experiments-only palette.runs",
    [entry({ to: "palette.runs" })],
    "flag-target",
  ],
  ["legacy L1 claim list.real", [entry({ to: "list.real" })], "slot-target"],
  ["L6 shell.protoOnly", [entry({ to: "shell.protoOnly" })], "slot-target"],
];
// Lane additions take the same rule, from their declaration and their
// registry surface alike: [name, declaration, surface, code].
const ungatedLane = [
  [
    "a shared-host surface",
    declaration(),
    surface({ anyOf: ["list.organize.query"] }),
    "shared-host-target",
  ],
  [
    "a shared-host declaration",
    declaration({ anyOf: ["list.organize.query"] }),
    surface(),
    "shared-host-target",
  ],
  [
    "a shown declaration",
    declaration({ ships: "shown" }),
    surface(),
    "shown-target",
  ],
  [
    "a shown surface",
    declaration(),
    surface({ ships: "shown" }),
    "shown-target",
  ],
  [
    "an experimentsBasic surface",
    declaration(),
    surface({ ships: "experimentsBasic" }),
    "flag-target",
  ],
  [
    "an experiments declaration",
    declaration({ experiments: true }),
    surface({ experiments: true }),
    "flag-target",
  ],
  [
    "a child of a hidden parent",
    declaration({ parent: "list.organize.query" }),
    surface({ parent: "list.organize.query" }),
    "parent-target",
  ],
  [
    "a legacy slot",
    declaration({ slot: "L6" }),
    surface({ madeRealBy: "L6" }),
    "slot-target",
  ],
  [
    "a declaration in another slot",
    declaration({ slot: "GRID" }),
    surface(),
    "slot-target",
  ],
];

describe("P0 dataset capability ownership declarations", () => {
  it("keeps the P0 ownership map when no move is declared", () => {
    expect(resolve([])).toEqual(P0_CAP_OWNERS);
  });

  it("moves only the declared capability and never mutates P0", () => {
    expect(resolve([entry()])).toEqual({
      ...P0_CAP_OWNERS,
      listSavedViews: TARGET,
    });
    expect(P0_CAP_OWNERS.listSavedViews).toBe("list.organize.query");
    expect(resolve([]).listSavedViews).toBe("list.organize.query");
  });

  it("accepts a P0 baseline or addition without a lane declaration", () => {
    for (const to of ["dataset.savedViews", "list.organize.metadata"])
      expect(resolve([entry({ to })], []).listSavedViews).toBe(to);
  });

  it("requires new targets to be declared", () => {
    expect(() => resolve([entry()], [])).toThrow("unknown-id");
  });

  it("requires a declared target to be in the registry", () => {
    expect(() => resolve([entry()], declarations, LAUNCH_SURFACES)).toThrow(
      "missing-surface",
    );
  });

  it("does not let lane declarations admit retired or Results targets", () => {
    for (const [to, code] of [
      ["dataset.collab", "retired-id"],
      ["exp.results.new", "results-id"],
    ])
      expect(() => resolve([entry({ to })], [{ id: to }])).toThrow(code);
  });

  it("does not let a lane declaration re-gate a P0 surface", () => {
    expect(() =>
      resolve(
        [entry({ to: "shell.real" })],
        [declaration({ id: "shell.real" })],
      ),
    ).toThrow("shown-target");
  });

  it("allows independent moves of distinct capabilities", () => {
    expect(
      resolve([
        entry(),
        entry({ cap: "listFilters", to: "dataset.savedViews" }),
      ]),
    ).toEqual({
      ...P0_CAP_OWNERS,
      listSavedViews: TARGET,
      listFilters: "dataset.savedViews",
    });
  });

  it("coexists with literal-site entries without changing their pins", () => {
    const sites = [["Gate.jsx", ["dataset.schema"]]];
    const wire = {
      file: "Gate.jsx",
      lane: "T-GRID",
      feature: "F47",
      expected: ["dataset.schema", "dataset.savedViews"],
    };
    for (const entries of [
      [entry(), wire],
      [wire, entry()],
    ]) {
      expect(
        resolveWiringSites(sites, entries, {
          laneDeclarations: declarations,
          surfaces: SURFACES,
        }),
      ).toEqual([[wire.file, wire.expected]]);
      expect(resolve(entries).listSavedViews).toBe(TARGET);
    }
  });

  it("still rejects unknown entry kinds and malformed lists", () => {
    expect(() => resolveWiringSites([], [entry({ kind: "cap" })])).toThrow(
      "unknown-field",
    );
    expect(() => resolve(null)).toThrow("not-list");
  });

  it.each(invalid)("rejects %s", (_name, entries, code) => {
    expect(() => resolve(entries)).toThrow(code);
    // Site consumers must validate cap entries too, never silently skip them.
    expect(() =>
      resolveWiringSites([], entries, {
        laneDeclarations: declarations,
        surfaces: SURFACES,
      }),
    ).toThrow(code);
  });

  it.each(ungatedLane)(
    "rejects a lane addition with %s",
    (_name, declared, added, code) => {
      const surfaces = [...LAUNCH_SURFACES, added];
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
