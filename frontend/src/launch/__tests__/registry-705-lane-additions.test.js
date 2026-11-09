import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it, vi } from "vitest";
import p0 from "../__fixtures__/registry-p0-options-705.json";
import {
  moduleWithClaims,
  sourceWithClaims,
  flowsWithWalks,
} from "./registryClaimSets";
import {
  RUN_CONTROL,
  moduleWithLaneAdditions,
} from "./registryLaneAdditionSets";

const SOURCE = readFileSync(
  resolve(process.cwd(), "src/launch/madeReal.js"),
  "utf8",
);
const FLOWS = readFileSync(resolve(process.cwd(), "../e2e/FLOWS.md"), "utf8");
const STATES = [
  [true, false],
  [true, true],
  [false, false],
  [false, true],
];
const claim = (id = RUN_CONTROL.id, slot = "XP") => ({
  claims: {
    [slot]: [{ id, comment: "LANE-E2E-999: two-tenant fixture walk" }],
  },
  flows: ["### LANE-E2E-999 - two-tenant fixture walk"],
});

// `lane` stands in for additions a lane really ships: the registry carries
// them (with `laneEdit`) the way a lane's launchSurfaces.js would, and the
// case's entries are still measured against the P0 baseline alone. The
// tree's real ownership moves (registry-wiring-additions-705.json) name the
// tree's real additions, which a case drops, so a case reads none of them.
async function loadWith(
  entries = [RUN_CONTROL],
  set = {},
  edit,
  { lane = [], laneEdit } = {},
) {
  vi.resetModules();
  vi.doMock("../__fixtures__/registry-lane-additions-705.json", () => ({
    default: entries,
  }));
  vi.doMock("../__fixtures__/registry-wiring-additions-705.json", () => ({
    default: [],
  }));
  vi.doMock("../launchSurfaces", async (original) => {
    const actual = await original();
    const shipped = lane.length
      ? moduleWithLaneAdditions(actual, lane, laneEdit)
      : actual;
    return moduleWithLaneAdditions(shipped, entries, edit, lane);
  });
  vi.doMock("../madeReal", async (original) =>
    moduleWithClaims(await original(), set),
  );
  const declarations = await import("./registryLaneAdditions");
  const contract = await import("./registryClaims");
  const registry = await import("../launchSurfaces");
  const madeReal = await import("../madeReal");
  const launchSwitch = await import("../launchSwitch");
  const entriesApi = {
    launchRedirect: (await import("../launchRoutes")).launchRedirect,
    sanitizeLaunchSearch: (await import("../launchSearch"))
      .sanitizeLaunchSearch,
  };
  const claims = contract.liveClaims(madeReal.MADE_REAL);
  const violations = () =>
    contract.claimViolations({
      ...madeReal,
      source: sourceWithClaims(SOURCE, set),
      flows: flowsWithWalks(FLOWS, set),
      live: madeReal.madeRealSurfaceIds(),
    });
  return {
    declarations,
    contract,
    registry,
    madeReal,
    launchSwitch,
    entriesApi,
    claims,
    violations,
  };
}

function drift(loaded, wrap = (shown) => shown) {
  const { contract, launchSwitch, madeReal, claims, entriesApi } = loaded;
  const found = { surfaces: [], options: [], entries: [] };
  for (const [launch, experimentsBasic] of STATES) {
    const state = {
      launch,
      experimentsBasic,
      madeReal: madeReal.madeRealSurfaceIds(),
    };
    const shown = wrap(launchSwitch.surfaceShownIn);
    const at = (d) => ({ ...d, launch, experimentsBasic });
    found.surfaces.push(...contract.surfaceDrift(shown, state, claims).map(at));
    found.options.push(...contract.optionDrift(shown, state, claims).map(at));
    found.entries.push(
      ...contract.entryDrift(entriesApi, state, claims).map(at),
    );
  }
  return found;
}

const EMPTY_DRIFT = { surfaces: [], options: [], entries: [] };

afterEach(() => {
  vi.unstubAllEnvs();
  vi.doUnmock("../launchSurfaces");
  vi.doUnmock("../madeReal");
  vi.doUnmock("../__fixtures__/registry-lane-additions-705.json");
  vi.doUnmock("../__fixtures__/registry-wiring-additions-705.json");
  vi.resetModules();
});

describe("lane additions: hidden declarations, provenance and live claims", () => {
  it.each([false, true])(
    "accepts run control with a valid declaration (claimed=%s)",
    async (claimed) => {
      const loaded = await loadWith([RUN_CONTROL], claimed ? claim() : {});
      expect(loaded.declarations.laneAdditionViolations()).toEqual([]);
      expect(loaded.violations()).toEqual([]);
      expect(drift(loaded)).toEqual(EMPTY_DRIFT);
      for (const [launch, experimentsBasic] of STATES) {
        const state = {
          launch,
          experimentsBasic,
          madeReal: loaded.madeReal.madeRealSurfaceIds(),
        };
        loaded.launchSwitch.setLaunchOverrideForTest(state);
        // F06 keeps its independent launch hold even when claimed.
        vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false");
        expect(loaded.launchSwitch.isSurfaceShown(RUN_CONTROL.id)).toBe(
          !launch,
        );
        // Enabled-path control: the existing test-only qualification profile.
        vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
        expect(loaded.launchSwitch.isSurfaceShown(RUN_CONTROL.id)).toBe(
          !launch || (claimed && experimentsBasic),
        );
        vi.unstubAllEnvs();
        expect(loaded.contract.baselineShown(RUN_CONTROL.id, state)).toBe(
          !launch,
        );
        if (launch)
          expect(loaded.launchSwitch.isSurfaceShown("exp.results.matrix")).toBe(
            false,
          );
      }
      loaded.launchSwitch.setLaunchOverrideForTest(null);
    },
  );

  it.each([
    {
      id: "dataset.duplicateMove",
      slot: "ORG",
      splitFrom: "dataset.switcher",
      options: ["copy:duplicate-move"],
    },
    { id: "dataset.list.columns", slot: "ORG" },
    {
      id: "eval.cardChips",
      slot: "EVAL",
      splitFrom: "dataset.evalVersionNotice",
      options: ["wiz:card-chips"],
    },
    {
      id: "eval.readsTable",
      slot: "EVAL",
      splitFrom: "exp.new.readsTable",
      options: ["wiz:reads-table"],
    },
    {
      id: "shell.notFound.moved",
      slot: "EVAL",
      splitFrom: "shell.notFound",
      options: ["header:moved-banner"],
    },
  ])("accepts hidden lane split $id", async (entry) => {
    const loaded = await loadWith([{ ...entry, ships: "hidden" }]);
    expect(loaded.declarations.laneAdditionViolations()).toEqual([]);
    expect(loaded.violations()).toEqual([]);
    expect(drift(loaded)).toEqual(EMPTY_DRIFT);
  });

  it("measures a case against the P0 ids, never this tree's real lane additions", async () => {
    const loaded = await loadWith([]);
    const ids = loaded.registry.LAUNCH_SURFACES.map((s) => s.id);
    // Census section A / NOHIDE: classic is retained independently of NEW
    // lane additions. SDK run traces (e9f0a2d0dd) also postdate P0 but are not
    // a declared lane addition; the pinned P0 inventory remains unchanged.
    expect(ids.sort()).toEqual(
      [
        ...Object.keys(p0.surfaces),
        "exp.classic",
        "dataset.sdkRunTraces",
      ].sort(),
    );
    expect(loaded.declarations.laneAdditionViolations()).toEqual([]);
    expect(loaded.violations()).toEqual([]);
    expect(drift(loaded)).toEqual(EMPTY_DRIFT);
  });

  // A lane's real split never shares an option with a case's split: it is
  // dropped and its options go back to their P0 source first.
  it.each([
    [
      "the same option from the same source",
      {
        id: "lane.cardChips",
        slot: "EVAL",
        parent: "exp.new",
        experiments: true,
        splitFrom: "dataset.evalVersionNotice",
        options: ["wiz:card-chips"],
      },
      {
        id: "eval.cardChips",
        slot: "EVAL",
        splitFrom: "dataset.evalVersionNotice",
        options: ["wiz:card-chips"],
      },
    ],
    [
      "another option from the same source",
      {
        id: "lane.notFoundPage",
        slot: "EVAL",
        splitFrom: "shell.notFound",
        options: ["state:not-found"],
      },
      {
        id: "shell.notFound.moved",
        slot: "EVAL",
        splitFrom: "shell.notFound",
        options: ["header:moved-banner"],
      },
    ],
    [
      "an option from another source",
      {
        id: "lane.savedViews",
        slot: "ORG",
        splitFrom: "list.organize.query",
        options: ["org:saved-list-views"],
      },
      RUN_CONTROL,
    ],
  ])(
    "isolates a case from a lane's real split of %s",
    async (_name, real, entry) => {
      const lane = [{ ...real, ships: "hidden" }];
      const [option] = real.options;
      const actual = await vi.importActual("../launchSurfaces");
      // The option's P0 owners, with the split source handed to `to`.
      const owners = (to = real.splitFrom) =>
        Object.keys(p0.surfaces)
          .filter((id) =>
            p0.surfaces[id].some((p) => actual.matchesGlob(p, option)),
          )
          .map((id) => (id === real.splitFrom ? to : id))
          .sort();
      const ownersIn = (registry) =>
        registry
          .surfacesOfOption(option)
          .map((s) => s.id)
          .sort();
      expect(owners()).toContain(real.splitFrom);
      expect(ownersIn(moduleWithLaneAdditions(actual, lane))).toEqual(
        owners(real.id),
      );
      const loaded = await loadWith(
        [{ ...entry, ships: "hidden" }],
        {},
        undefined,
        { lane },
      );
      expect(loaded.registry.SURFACE_BY_ID[real.id]).toBeUndefined();
      expect(ownersIn(loaded.registry)).toEqual(
        owners(entry.options.includes(option) ? entry.id : undefined),
      );
      expect(loaded.declarations.laneAdditionViolations()).toEqual([]);
      expect(loaded.violations()).toEqual([]);
      expect(drift(loaded)).toEqual(EMPTY_DRIFT);
    },
  );

  it("takes a lane's real child out of the shared host it joined", async () => {
    const real = { id: "lane.activity", slot: "COLLAB", ships: "hidden" };
    const laneEdit = (surfaces) => {
      const host = surfaces.find((s) => s.id === "dataset.rowTab.activity");
      host.anyOf = [...host.anyOf, real.id];
    };
    const loaded = await loadWith([RUN_CONTROL], {}, undefined, {
      lane: [real],
      laneEdit,
    });
    const { SURFACE_BY_ID } = loaded.registry;
    expect(SURFACE_BY_ID[real.id]).toBeUndefined();
    // The host's P0 parts.
    expect(SURFACE_BY_ID["dataset.rowTab.activity"].anyOf).toEqual([
      "dataset.collab.comments",
      "dataset.rowExtras.activity",
    ]);
    expect(loaded.violations()).toEqual([]);
    expect(drift(loaded)).toEqual(EMPTY_DRIFT);
  });

  it.each([false, true])(
    "isolates a same-id shared-host child before the case's own edit (edit=%s)",
    async (editHost) => {
      const entry = {
        id: "dataset.activity.extra",
        slot: "COLLAB",
        ships: "hidden",
      };
      const hostId = "dataset.rowTab.activity";
      const p0Parts = ["dataset.collab.comments", "dataset.rowExtras.activity"];
      const attach = (surfaces) => {
        const host = surfaces.find((s) => s.id === hostId);
        host.anyOf = [...host.anyOf, entry.id];
      };
      const actual = await vi.importActual("../launchSurfaces");
      const shipped = moduleWithLaneAdditions(actual, [entry], attach);
      expect(shipped.SURFACE_BY_ID[hostId].anyOf).toEqual([
        ...p0Parts,
        entry.id,
      ]);
      const loaded = await loadWith(
        [entry],
        claim(entry.id, entry.slot),
        editHost ? attach : undefined,
        { lane: [entry], laneEdit: attach },
      );
      expect(loaded.registry.SURFACE_BY_ID[entry.id]).toBeDefined();
      expect(loaded.registry.SURFACE_BY_ID[hostId].anyOf).toEqual(
        editHost ? [...p0Parts, entry.id] : p0Parts,
      );
      expect(loaded.declarations.laneAdditionViolations()).toEqual([]);
      expect(loaded.violations()).toEqual([]);
      expect(drift(loaded)).toEqual(EMPTY_DRIFT);
      const state = {
        launch: true,
        experimentsBasic: false,
        madeReal: loaded.madeReal.madeRealSurfaceIds(),
      };
      expect(loaded.launchSwitch.surfaceShownIn(entry.id, state)).toBe(true);
      expect(loaded.launchSwitch.surfaceShownIn(hostId, state)).toBe(editHost);
      // Injection must not mutate the registry it starts from.
      expect(shipped.SURFACE_BY_ID[hostId].anyOf).toEqual([
        ...p0Parts,
        entry.id,
      ]);
    },
  );

  it("keeps a claimed child's hidden parent and experiments flag gates", async () => {
    const entry = { ...RUN_CONTROL, parent: "exp.new.scope" };
    const childOnly = await loadWith([entry], claim());
    expect(childOnly.declarations.laneAdditionViolations()).toEqual([]);
    expect(drift(childOnly)).toEqual(EMPTY_DRIFT);
    const state = {
      launch: true,
      experimentsBasic: true,
      madeReal: childOnly.madeReal.madeRealSurfaceIds(),
    };
    expect(childOnly.launchSwitch.surfaceShownIn(entry.id, state)).toBe(false);
    const set = claim();
    set.claims.XP.push({ id: "exp.new.scope", comment: "LANE-E2E-999" });
    const both = await loadWith([entry], set);
    expect(both.violations()).toEqual([]);
    expect(drift(both)).toEqual(EMPTY_DRIFT);
    const bothState = {
      ...state,
      madeReal: both.madeReal.madeRealSurfaceIds(),
    };
    // A claim cannot lift F05's independent hold, including for descendants.
    expect(both.launchSwitch.surfaceShownIn(entry.id, bothState)).toBe(false);
    expect(
      both.launchSwitch.surfaceShownIn(entry.id, {
        ...bothState,
        launch: false,
      }),
    ).toBe(true);
    // Enabled-path control uses the existing test-only qualification profile.
    vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
    expect(both.launchSwitch.surfaceShownIn(entry.id, bothState)).toBe(true);
    expect(
      both.launchSwitch.surfaceShownIn(entry.id, {
        ...bothState,
        experimentsBasic: false,
      }),
    ).toBe(false);
  });

  it("lets the shared Activity host follow a new child without claiming the host", async () => {
    const entry = {
      id: "dataset.activity.extra",
      slot: "COLLAB",
      ships: "hidden",
    };
    const edit = (surfaces) => {
      const host = surfaces.find((s) => s.id === "dataset.rowTab.activity");
      host.anyOf = [...host.anyOf, entry.id];
    };
    const loaded = await loadWith([entry], claim(entry.id, entry.slot), edit);
    expect(loaded.declarations.laneAdditionViolations()).toEqual([]);
    expect(loaded.violations()).toEqual([]);
    expect(drift(loaded)).toEqual(EMPTY_DRIFT);
    const state = {
      launch: true,
      experimentsBasic: false,
      madeReal: loaded.madeReal.madeRealSurfaceIds(),
    };
    expect(
      loaded.launchSwitch.surfaceShownIn("dataset.rowTab.activity", state),
    ).toBe(true);
    const bad = await loadWith(
      [entry],
      claim("dataset.rowTab.activity", "COLLAB"),
      edit,
    );
    expect(bad.violations().map((v) => v.code)).toContain("shared-host");
  });

  const invalid = [
    ["duplicate id", [RUN_CONTROL, RUN_CONTROL], "duplicate-id"],
    [
      "baseline id",
      [{ ...RUN_CONTROL, id: "dataset.switcher" }],
      "existing-id",
    ],
    [
      "retired baseline id",
      [{ ...RUN_CONTROL, id: "exp.results" }],
      "existing-id",
    ],
    [
      "P0 addition id",
      [{ ...RUN_CONTROL, id: "dataset.export" }],
      "existing-id",
    ],
    [
      "P0 split id",
      [{ ...RUN_CONTROL, id: "dataset.collab.tags" }],
      "existing-id",
    ],
    [
      "Results id",
      [{ ...RUN_CONTROL, id: "exp.results.newChild" }],
      "results-id",
    ],
    ["RES slot", [{ ...RUN_CONTROL, slot: "RES" }], "invalid-slot"],
    ["unknown slot", [{ ...RUN_CONTROL, slot: "OTHER" }], "invalid-slot"],
    [
      "unknown splitFrom",
      [{ ...RUN_CONTROL, splitFrom: "missing.surface" }],
      "unknown-source",
    ],
    [
      "option not on source",
      [{ ...RUN_CONTROL, options: ["create:upload-file"] }],
      "option-not-on-source",
    ],
    [
      "unknown option",
      [{ ...RUN_CONTROL, options: ["missing:option"] }],
      "unknown-option",
    ],
    [
      "missing splitFrom",
      [{ ...RUN_CONTROL, splitFrom: undefined }],
      "missing-source",
    ],
    ["ships not hidden", [{ ...RUN_CONTROL, ships: "shown" }], "not-hidden"],
    [
      "declaration is not a claim",
      [{ ...RUN_CONTROL, walk: "LANE-E2E-999" }],
      "declaration-walk",
    ],
    [
      "unknown parent",
      [{ ...RUN_CONTROL, parent: "missing.parent" }],
      "unknown-parent",
    ],
    [
      "parent cycle",
      [{ ...RUN_CONTROL, parent: RUN_CONTROL.id }],
      "parent-cycle",
    ],
    [
      "invalid experiments",
      [{ ...RUN_CONTROL, experiments: "true" }],
      "invalid-experiments",
    ],
    [
      "duplicate option",
      [
        {
          ...RUN_CONTROL,
          options: ["action:stop-resume", "action:stop-resume"],
        },
      ],
      "duplicate-option",
    ],
  ];
  it.each(invalid)("rejects %s", async (_name, entries, code) => {
    const loaded = await loadWith(entries);
    const found = loaded.declarations.laneAdditionViolations();
    expect(found.map((v) => v.code)).toContain(code);
  });

  it.each([
    [
      "surface-slot",
      (s) => {
        s.madeRealBy = "ORG";
      },
    ],
    [
      "surface-not-hidden",
      (s) => {
        s.ships = "shown";
      },
    ],
    [
      "surface-parent",
      (s) => {
        s.parent = "exp.new";
      },
    ],
    [
      "surface-experiments",
      (s) => {
        s.experiments = false;
      },
    ],
    [
      "surface-options",
      (s) => {
        s.options = [];
      },
    ],
  ])("rejects live metadata drift: %s", async (code, mutate) => {
    const loaded = await loadWith([RUN_CONTROL], {}, (surfaces) =>
      mutate(surfaces.find((s) => s.id === RUN_CONTROL.id)),
    );
    expect(
      loaded.declarations.laneAdditionViolations().map((v) => v.code),
    ).toContain(code);
  });

  it("rejects an option still carried by splitFrom", async () => {
    const loaded = await loadWith([RUN_CONTROL], {}, (surfaces) => {
      surfaces
        .find((s) => s.id === RUN_CONTROL.splitFrom)
        .options.push("action:stop-resume");
    });
    expect(
      loaded.declarations.laneAdditionViolations().map((v) => v.code),
    ).toContain("option-owners");
  });

  it.each([
    ["wrong-slot", { ...claim(), claims: { ORG: claim().claims.XP } }],
    ["missing-walk", { claims: { XP: [{ id: RUN_CONTROL.id }] } }],
    ["unknown-walk", { ...claim(), flows: [] }],
  ])("rejects a new surface claim with %s", async (code, set) => {
    const loaded = await loadWith([RUN_CONTROL], set);
    expect(loaded.violations().map((v) => v.code)).toContain(code);
  });

  it("rejects re-pointed options becoming visible without a claim", async () => {
    const loaded = await loadWith();
    const found = drift(
      loaded,
      (shown) => (id, state) => id === RUN_CONTROL.id || shown(id, state),
    );
    expect(found.options.map((v) => v.option)).toContain("action:stop-resume");
    expect(found.surfaces.map((v) => v.id)).toContain(RUN_CONTROL.id);
  });

  it("also rejects hiding a baseline-shown option behind an unclaimed addition", async () => {
    const loaded = await loadWith([
      {
        id: "list.openExtra",
        slot: "ORG",
        ships: "hidden",
        splitFrom: "list.real",
        options: ["list:open"],
      },
    ]);
    expect(loaded.declarations.laneAdditionViolations()).toEqual([]);
    expect(drift(loaded).options.map((v) => v.option)).toContain("list:open");
  });
});
