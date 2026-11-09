import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it } from "vitest";
import before from "../__fixtures__/registry-before-705.json";
import optionsBefore from "../__fixtures__/registry-options-before-705.json";
import splits from "../__fixtures__/registry-splits-705.json";
import inventory from "../__fixtures__/inventory-classes.json";
import {
  LAUNCH_SURFACES,
  ROW_TAB_SURFACES,
  SURFACE_BY_ID,
  surfacesOfOption,
} from "../launchSurfaces";
import { MADE_REAL, MADE_REAL_PATHS, madeRealSurfaceIds } from "../madeReal";
import {
  launchState,
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "../launchSwitch";
import { launchRedirect } from "../launchRoutes";
import { sanitizeLaunchSearch } from "../launchSearch";
import { experimentSurfaceOf } from "src/sections/experiment-detail-classic/experimentsBasic";
import {
  ALL_ON_EXPERIMENTS_BASIC,
  NEW_SLOTS,
  NOHIDE_IMPORT_SURFACES,
  claimViolations,
  entryDrift,
  liveClaims,
  optionDrift,
  surfaceDrift,
} from "./registryClaims";
import {
  P0_ADDITIONS,
  laneAdditions,
  laneAdditionViolations,
} from "./registryLaneAdditions";

// P0 pinned every surface, option, route and deep-link key to its base
// state with empty WORKS slots. That baseline stays immutable: a WORKS
// lane's proven claim may show its own surface where the switch applies it,
// and nothing else may move (registryClaims.js; fixture claim sets in
// registry-705-claims.test.js).
const slots = NEW_SLOTS;
// SDK run traces arrived after the pinned P0 census (e9f0a2d0dd); include
// its literal id in the hidden-addition checks without changing the baseline.
const additions = [
  ...P0_ADDITIONS,
  ...laneAdditions.map((entry) => entry.id),
  "dataset.sdkRunTraces",
];
const read = (path) =>
  readFileSync(resolve(process.cwd(), "src", path), "utf8");
const FLOWS = readFileSync(
  resolve(process.cwd(), "..", "e2e", "FLOWS.md"),
  "utf8",
);
const stateOf = (launch, experimentsBasic) => ({
  launch,
  experimentsBasic,
  madeReal: madeRealSurfaceIds(),
});
const STATES = [
  [true, false],
  [true, true],
  [false, false],
  [false, true],
];
// The live WORKS claims (the real slots; empty on the P0 branch).
const works = liveClaims(MADE_REAL);

afterEach(() => setLaunchOverrideForTest(null));

describe("P0 registry: immutable baseline plus live WORKS claims", () => {
  it("validates append-only hidden lane additions against P0 option ownership", () => {
    expect(laneAdditionViolations()).toEqual([]);
  });

  it.each(STATES)(
    "enumerates every old and new id; only live claims move visibility (launch=%s, basic=%s)",
    (launch, basic) => {
      const state = stateOf(launch, basic);
      const covered = new Set();
      for (const old of before.surfaces) {
        for (const id of splits[old.id] ?? [old.id]) {
          expect(SURFACE_BY_ID[id], id).toBeDefined();
          // DSX J09 / 767/769/770 restores only the named import doors; the
          // all-on flip ships the experimentsBasic family shown (behind the flag).
          expect(SURFACE_BY_ID[id].ships, id).toBe(
            NOHIDE_IMPORT_SURFACES.has(id) || ALL_ON_EXPERIMENTS_BASIC.has(id)
              ? "shown"
              : old.ships,
          );
          covered.add(id);
        }
      }
      for (const id of additions) {
        expect(SURFACE_BY_ID[id].ships).toBe(
          NOHIDE_IMPORT_SURFACES.has(id) ? "shown" : "hidden",
        );
        covered.add(id);
      }
      // Census A/B: exact legacy option split; NEW child families stay hidden.
      expect(SURFACE_BY_ID["exp.classic"]).toMatchObject({
        ships: "shown",
        madeRealBy: "XP",
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
      });
      expect(SURFACE_BY_ID["exp.classic"].parent).toBeUndefined();
      expect(SURFACE_BY_ID["exp.classic"].experiments).toBeUndefined();
      covered.add("exp.classic");
      expect([...covered].sort()).toEqual(
        LAUNCH_SURFACES.map((s) => s.id).sort(),
      );
      // Every split keeps its base's visibility and every addition stays
      // hidden, except where a live claim applies.
      expect(surfaceDrift(surfaceShownIn, state, works)).toEqual([]);
      expect(surfaceShownIn("dataset.runPromptEdit", state)).toBe(
        !launch || works.has("dataset.runPromptEdit"),
      );
    },
  );

  it("keeps every inventory option's hidden state unless a live claim reveals it (708 options, flag OFF and ON)", () => {
    const ids = Object.keys(inventory.classes);
    expect(ids).toHaveLength(optionsBefore.options);
    for (const [launch, basic] of STATES)
      expect(
        optionDrift(surfaceShownIn, stateOf(launch, basic), works),
        `launch=${launch} basic=${basic}`,
      ).toEqual([]);
  });

  it("preserves all original route and search outcomes outside live claims, including split row tabs", () => {
    for (const [launch, basic] of STATES)
      expect(
        entryDrift(
          { launchRedirect, sanitizeLaunchSearch },
          stateOf(launch, basic),
          works,
        ),
        `launch=${launch} basic=${basic}`,
      ).toEqual([]);
  });

  it("keeps legacy claims byte-for-byte and validates every live claim in the isolated slots", () => {
    const source = read("launch/madeReal.js");
    expect(source).toContain(
      `export const MADE_REAL = Object.freeze({${before.claimSource}`,
    );
    expect(source).toContain(
      `export const MADE_REAL_PATHS = Object.freeze({${before.pathSource}`,
    );
    for (const [slot, claims] of Object.entries(before.claims))
      expect(MADE_REAL[slot]).toEqual(claims);
    for (const [slot, paths] of Object.entries(before.paths))
      expect(MADE_REAL_PATHS[slot]).toEqual(paths);
    for (const slot of slots) {
      expect(
        source.match(new RegExp(`BEGIN ${slot} claims`, "g")),
      ).toHaveLength(1);
      expect(source.match(new RegExp(`END ${slot} claims`, "g"))).toHaveLength(
        1,
      );
      expect(source.match(new RegExp(`BEGIN ${slot} paths`, "g"))).toHaveLength(
        1,
      );
      expect(source.match(new RegExp(`END ${slot} paths`, "g"))).toHaveLength(
        1,
      );
    }
    for (const registry of [MADE_REAL, MADE_REAL_PATHS]) {
      const values = Object.values(registry).flat();
      expect(new Set(values).size).toBe(values.length);
    }
    for (const surface of LAUNCH_SURFACES)
      expect(Object.keys(MADE_REAL)).toContain(surface.madeRealBy);
    // Each live claim: a known, unshared surface of its own slot, never a
    // legacy claim or a Results activation id, claimed once, with a
    // two-tenant walk id that e2e/FLOWS.md lists; paths only beside a claim.
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    expect(
      claimViolations({
        MADE_REAL,
        MADE_REAL_PATHS,
        source,
        flows: FLOWS,
        live: launchState().madeReal,
      }),
    ).toEqual([]);
  });

  it("allows each Results child route independently without exposing sibling routes", () => {
    for (const picked of ["matrix", "paths", "summary", "traces"]) {
      const state = {
        ...stateOf(true, true),
        madeReal: new Set([`exp.results.${picked}`]),
      };
      for (const tab of ["matrix", "paths", "summary", "traces"]) {
        expect(
          Boolean(
            launchRedirect(
              { pathname: `/dashboard/develop/experiment/e1/${tab}` },
              state,
            ),
          ),
          // J09: /summary is the legacy destination, not Results activation.
        ).toBe(tab !== "summary" && tab !== picked);
      }
      expect(surfaceShownIn("exp.results.iterateDock", state)).toBe(false);
      expect(surfaceShownIn("dataset.collab.sendToQa", state)).toBe(false);
      for (const id of additions.filter((x) => x.startsWith("exp.results.")))
        expect(surfaceShownIn(id, state)).toBe(false);
    }
  });

  it("puts each re-mapped option under its plan feature's surface only", () => {
    // option -> [surface, owning slot] (plan.json F03, F37, F41, F47, F50,
    // F51, F55, F56).
    const owners = {
      "review:summary": ["dataset.collab.comments", "COLLAB"],
      "review:start": ["dataset.collab.sendToQa", "COLLAB"],
      "review:submit": ["dataset.collab.sendToQa", "COLLAB"],
      "review:results-review-tab": ["dataset.collab.sendToQa", "COLLAB"],
      "md:reason": ["dataset.filtersSystem.expressions", "GRID"],
      "access:gates": ["list.organize.folders", "ORG"],
      "synthetic:generate-more-like-these": ["synthetic.extras", "IN"],
      "synthetic:kb-goldens": ["synthetic.extras", "IN"],
      "export:dataset": ["dataset.export", "GRID"],
      "training:export": ["dataset.splits", "GRID"],
    };
    for (const [option, [id, slot]] of Object.entries(owners)) {
      expect(
        surfacesOfOption(option).map((s) => s.id),
        option,
      ).toEqual([id]);
      expect(SURFACE_BY_ID[id].madeRealBy, option).toBe(slot);
    }
    // The wizard's description is F03's: its own XP claim under exp.new.
    expect(surfacesOfOption("exp:description").map((s) => s.id)).toEqual([
      "exp.new",
      "exp.rename",
    ]);
    expect(SURFACE_BY_ID["exp.rename"]).toMatchObject({
      parent: "exp.new",
      experiments: true,
      madeRealBy: "XP",
    });
    // Training export stays with splits (plan F50): no separate id.
    expect(SURFACE_BY_ID["dataset.trainingExport"]).toBeUndefined();
  });

  it("a shared host follows its features and is never claimed itself", () => {
    const hosts = LAUNCH_SURFACES.filter((s) => s.anyOf);
    expect(hosts.map((s) => s.id)).toEqual(["dataset.rowTab.activity"]);
    expect(ROW_TAB_SURFACES.activity).toBe("dataset.rowTab.activity");
    const claimed = Object.values(MADE_REAL).flat();
    for (const host of hosts) {
      expect(claimed).not.toContain(host.id);
      expect(host.options).toEqual([]);
      const state = (ids) => ({
        ...stateOf(true, true),
        madeReal: new Set(ids),
      });
      expect(surfaceShownIn(host.id, state([]))).toBe(false);
      expect(surfaceShownIn(host.id, state([host.id]))).toBe(false);
      for (const part of host.anyOf) {
        expect(SURFACE_BY_ID[part].anyOf, part).toBeUndefined();
        expect(surfaceShownIn(host.id, state([part])), part).toBe(true);
      }
      expect(surfaceShownIn(host.id, stateOf(false, false))).toBe(true);
    }
  });

  it("reserves Results ids for main without activating the DSX wrapper", () => {
    const ids = LAUNCH_SURFACES.filter((s) => s.id.startsWith("exp.results."));
    expect(ids).toHaveLength(8);
    for (const surface of ids) {
      expect(surface).toMatchObject({ ships: "hidden", madeRealBy: "RES" });
      expect(madeRealSurfaceIds().has(surface.id)).toBe(false);
    }
    // Even an attempted Results opt-in cannot change the base selector in P0.
    for (const prototype of [false, true]) {
      for (const basic of [false, true]) {
        for (const results of [false, true]) {
          expect(experimentSurfaceOf({ prototype, basic, results })).toBe(
            prototype ? "dsx" : "classic",
          );
        }
      }
    }
  });
});
