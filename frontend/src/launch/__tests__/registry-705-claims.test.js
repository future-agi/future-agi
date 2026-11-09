import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixtures from "../__fixtures__/registry-claims-705.json";
import { LAUNCH_SURFACES } from "../launchSurfaces";
import {
  baselineShown,
  HELD_749,
  NOHIDE_IMPORT_SURFACES,
  entryDrift,
  expectedShown,
  liveClaims,
  optionDrift,
  surfaceDrift,
} from "./registryClaims";
import {
  flowsWithWalks,
  moduleWithClaims,
  sourceWithClaims,
} from "./registryClaimSets";
import { P0_PATH_OWNERS } from "./registryPathOwners";

// The registry contract for WORKS claims, driven by fixture claim sets. Each
// set is injected in place of madeReal.js (module and source) and the launch
// modules are loaded fresh, so the switch reads the claims exactly as a build
// does. The real slots are never edited.

const SOURCE = readFileSync(
  resolve(process.cwd(), "src", "launch", "madeReal.js"),
  "utf8",
);
const FLOWS = readFileSync(
  resolve(process.cwd(), "..", "e2e", "FLOWS.md"),
  "utf8",
);
const STATES = [
  [true, false],
  [true, true],
  [false, false],
  [false, true],
];

/** The launch modules, loaded fresh with `set` in the WORKS slots. */
async function loadWith(set) {
  vi.resetModules();
  vi.doMock("../madeReal", async (importOriginal) =>
    moduleWithClaims(await importOriginal(), set),
  );
  const madeReal = await import("../madeReal");
  const launchSwitch = await import("../launchSwitch");
  const { launchRedirect } = await import("../launchRoutes");
  const { sanitizeLaunchSearch } = await import("../launchSearch");
  const { claimViolations } = await import("./registryClaims");
  return {
    claimViolations,
    madeReal,
    launchSwitch,
    entries: { launchRedirect, sanitizeLaunchSearch },
    source: sourceWithClaims(SOURCE, set),
    flows: flowsWithWalks(FLOWS, set),
  };
}

/**
 * Everything the registry contract rejects for an injected claim set.
 * `wrap` stands in for a defective switch around surfaceShownIn.
 */
async function rejections(set, wrap = (shownIn) => shownIn) {
  const { madeReal, launchSwitch, entries, source, flows, claimViolations } =
    await loadWith(set);
  const claims = liveClaims(madeReal.MADE_REAL);
  launchSwitch.setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
  });
  const out = {
    slots: claimViolations({
      MADE_REAL: madeReal.MADE_REAL,
      MADE_REAL_PATHS: madeReal.MADE_REAL_PATHS,
      source,
      flows,
      live: launchSwitch.launchState().madeReal,
      // Synthetic cases describe P0 ownership, never a lane's live moves.
      pathOwners: P0_PATH_OWNERS,
    }),
    surfaces: [],
    options: [],
    entries: [],
  };
  const shownIn = wrap(launchSwitch.surfaceShownIn);
  for (const [launch, experimentsBasic] of STATES) {
    launchSwitch.setLaunchOverrideForTest({ launch, experimentsBasic });
    const state = launchSwitch.launchState();
    const at = (d) => ({ ...d, launch, experimentsBasic });
    out.surfaces.push(...surfaceDrift(shownIn, state, claims).map(at));
    out.options.push(...optionDrift(shownIn, state, claims).map(at));
    out.entries.push(...entryDrift(entries, state, claims).map(at));
  }
  launchSwitch.setLaunchOverrideForTest(null);
  return out;
}

const ids = (drift, key) => [...new Set(drift.map((d) => d[key]))].sort();

afterEach(() => {
  vi.doUnmock("../__fixtures__/registry-wiring-additions-705.json");
  vi.doUnmock("../madeReal");
  vi.resetModules();
});

describe("registry claim contract: injected claim sets", () => {
  it("loads the injected claims where the launch switch reads them", async () => {
    const set = fixtures.valid["t-in-upload"];
    const { madeReal, launchSwitch, source } = await loadWith(set);
    expect(madeReal.MADE_REAL.IN).toEqual(["dataset.upload"]);
    expect(madeReal.madeRealSurfaceIds().has("dataset.upload")).toBe(true);
    launchSwitch.setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
    });
    expect(launchSwitch.launchState().madeReal.has("dataset.upload")).toBe(
      true,
    );
    expect(launchSwitch.isSurfaceShown("dataset.upload")).toBe(true);
    launchSwitch.setLaunchOverrideForTest(null);
    expect(source).toContain(
      '    "dataset.upload", // DATA-E2E-003: two-organization CSV upload and readback.',
    );
  });

  it("keeps P0 plus the ruled classic/import and all-on exceptions for an empty claim set", async () => {
    expect(await rejections({})).toEqual({
      slots: [],
      surfaces: [],
      options: [],
      entries: [],
    });
    const none = new Set();
    for (const [launch, experimentsBasic] of STATES) {
      const state = { launch, experimentsBasic };
      for (const { id } of LAUNCH_SURFACES) {
        // Keep the historical fixture immutable. The all-on exceptions follow
        // experimentsBasic even with no WORKS claim, including explicit OFF.
        const allOn = [
          "exp.new",
          "dataset.tab.experiments",
          "dataset.experimentDefaults",
        ].includes(id);
        expect(expectedShown(id, state, none), id).toBe(
          allOn
            ? !launch || experimentsBasic
            : id === "exp.classic" ||
                NOHIDE_IMPORT_SURFACES.has(id) ||
                baselineShown(id, state),
        );
      }
    }
  });

  it("applies default ON and explicit false without WORKS claims, never opening Results", async () => {
    const { launchSwitch } = await loadWith({});
    for (const [env, shown] of [
      [{}, true],
      [{ VITE_DSX_EXPERIMENTS_BASIC: "false" }, false],
    ]) {
      const experimentsBasic = launchSwitch.experimentsBasicFromEnv(env);
      expect(experimentsBasic).toBe(shown);
      launchSwitch.setLaunchOverrideForTest({ launch: true, experimentsBasic });
      for (const id of [
        "exp.new",
        "dataset.tab.experiments",
        "dataset.experimentDefaults",
      ])
        expect(launchSwitch.isSurfaceShown(id), id).toBe(shown);
      expect(launchSwitch.isSurfaceShown("exp.classic")).toBe(true);
      for (const { id } of LAUNCH_SURFACES.filter((s) =>
        s.id.startsWith("exp.results."),
      ))
        expect(launchSwitch.isSurfaceShown(id), id).toBe(false);
    }
    launchSwitch.setLaunchOverrideForTest(null);
  });

  it.each(Object.entries(fixtures.valid))(
    "accepts %s, showing only what the claim applies to",
    async (_name, set) => {
      expect(await rejections(set)).toEqual({
        slots: [],
        surfaces: [],
        options: [],
        entries: [],
      });
      const { madeReal, launchSwitch } = await loadWith(set);
      const claims = liveClaims(madeReal.MADE_REAL);
      for (const id of set.shows) {
        for (const experimentsBasic of [false, true]) {
          const state = { launch: true, experimentsBasic };
          launchSwitch.setLaunchOverrideForTest(state);
          const shown =
            !HELD_749.has(id) && (!set.showsOnlyWithBasic || experimentsBasic);
          expect(launchSwitch.isSurfaceShown(id), id).toBe(shown);
          // The claim is what shows it: hidden at the P0 baseline.
          expect(baselineShown(id, state), id).toBe(false);
          expect(expectedShown(id, state, claims), id).toBe(shown);
        }
      }
      launchSwitch.setLaunchOverrideForTest(null);
    },
  );

  it.each(Object.entries(fixtures.invalid))(
    "rejects %s",
    async (_name, set) => {
      const found = await rejections(set);
      expect(found.slots).toEqual(set.rejects);
      expect(ids(found.surfaces, "id")).toEqual(set.drifts ?? []);
      expect(ids(found.options, "option")).toEqual(set.optionDrifts ?? []);
      expect(found.entries).toEqual([]);
    },
  );

  it("isolates every synthetic claim set from a live pathOwner move", async () => {
    const path = "/model-hub/datasets/{dataset_id}/splits/";
    vi.doMock("../__fixtures__/registry-wiring-additions-705.json", () => ({
      default: [
        {
          kind: "pathOwner",
          path,
          from: "dataset.splits",
          to: "dataset.savedViews",
          lane: "T-GRIDB",
          feature: "F03",
        },
      ],
    }));
    const { madeReal, source, flows, claimViolations } = await loadWith(
      fixtures.valid["grid-splits-with-path"],
    );
    const { resolvePathOwners } = await import("./registryPathOwners");
    expect(resolvePathOwners()[path]).toEqual(["dataset.savedViews"]);
    // Positive control: the live consumer must still honor the moved owner.
    expect(
      claimViolations({
        MADE_REAL: madeReal.MADE_REAL,
        MADE_REAL_PATHS: madeReal.MADE_REAL_PATHS,
        source,
        flows,
      }),
    ).toEqual([{ code: "path-without-surface", slot: "GRID", value: path }]);

    for (const [name, set] of Object.entries({
      empty: {},
      ...fixtures.valid,
      ...fixtures.invalid,
    })) {
      const found = await rejections(set);
      expect(found.slots, name).toEqual(set.rejects ?? []);
      expect(ids(found.surfaces, "id"), name).toEqual(set.drifts ?? []);
      expect(ids(found.options, "option"), name).toEqual(
        set.optionDrifts ?? [],
      );
      expect(found.entries, name).toEqual([]);
    }
  });

  it("rejects a switch that shows an unrelated surface with a valid claim", async () => {
    // A gate coupled to the upload claim also shows Synthetic extras.
    const coupled = (shownIn) => (id, state) =>
      (id === "synthetic.extras" && state.madeReal.has("dataset.upload")) ||
      shownIn(id, state);
    const found = await rejections(fixtures.valid["t-in-upload"], coupled);
    expect(found.slots).toEqual([]);
    expect(found.surfaces).toEqual([
      {
        id: "synthetic.extras",
        expected: false,
        actual: true,
        launch: true,
        experimentsBasic: false,
      },
      {
        id: "synthetic.extras",
        expected: false,
        actual: true,
        launch: true,
        experimentsBasic: true,
      },
    ]);
    expect(ids(found.options, "option")).toEqual([
      "synthetic:generate-more-like-these",
      "synthetic:kb-goldens",
    ]);
  });
});
