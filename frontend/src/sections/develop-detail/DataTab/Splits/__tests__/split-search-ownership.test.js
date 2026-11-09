import { afterEach, describe, expect, it, vi } from "vitest";
import { moduleWithLaneAdditions } from "src/launch/__tests__/registryLaneAdditionSets";

const SOURCE = "dataset.splits";
const TARGET = "exp.new.splitScope";
const PAGE = "/dashboard/develop/d1";
const declaration = {
  id: TARGET,
  slot: "XP",
  ships: "hidden",
  splitFrom: SOURCE,
  options: ["wiz:split-picker", "entry:run-on-rows:training-split"],
};

async function load(edit = (actual) => actual) {
  vi.resetModules();
  vi.doMock("src/launch/launchSurfaces", async (original) =>
    edit(await original()),
  );
  const contract = await import("src/launch/__tests__/registryClaims");
  const entries = await import("src/launch/launchSearch");
  const routes = await import("src/launch/launchRoutes");
  return { contract, entries: { ...entries, ...routes } };
}

const withSurfaces = (actual, surfaces) => ({
  ...actual,
  LAUNCH_SURFACES: surfaces,
  SURFACE_BY_ID: Object.fromEntries(
    surfaces.map((surface) => [surface.id, surface]),
  ),
});

const splitDrift = (contract, entries, state, claims) =>
  contract
    .entryDrift(entries, state, claims)
    .filter((row) => row.key === "split=1");

afterEach(() => {
  vi.doUnmock("src/launch/launchSurfaces");
  vi.resetModules();
});

describe("split search-key ownership contract", () => {
  it.each([false, true])(
    "follows the run-scope claim with dataset claim %s",
    async (datasetClaim) => {
      const { contract, entries } = await load();
      for (const runScopeClaim of [false, true]) {
        const claims = new Set([
          ...(datasetClaim ? [SOURCE] : []),
          ...(runScopeClaim ? [TARGET] : []),
        ]);
        for (const launch of [false, true]) {
          for (const experimentsBasic of [false, true]) {
            const state = { launch, experimentsBasic, madeReal: claims };
            expect(splitDrift(contract, entries, state, claims)).toEqual([]);
            expect(
              entries.sanitizeLaunchSearch("?split=1", PAGE, state).changed,
            ).toBe(launch);
            const wrong = {
              ...entries,
              sanitizeLaunchSearch: (...args) => ({
                ...entries.sanitizeLaunchSearch(...args),
                changed: false,
              }),
            };
            if (launch)
              expect(splitDrift(contract, wrong, state, claims)).toHaveLength(
                1,
              );
          }
        }
      }
    },
  );

  it("restores only the moved key to its baseline owner when isolating additions", async () => {
    const actual = await vi.importActual("src/launch/launchSurfaces");
    const original = actual.SURFACE_BY_ID[SOURCE].entries.search;
    const isolated = moduleWithLaneAdditions(actual, []);
    expect(isolated.SURFACE_BY_ID[TARGET]).toBeUndefined();
    expect(
      isolated.SURFACE_BY_ID[SOURCE].entries.search
        .map((rule) => rule.key)
        .sort(),
    ).toEqual(["split", "splits"]);
    expect(actual.SURFACE_BY_ID[SOURCE].entries.search).toBe(original);
    expect(original.map((rule) => rule.key)).toEqual(["splits"]);
    for (const surface of isolated.LAUNCH_SURFACES.filter(
      (surface) => surface.id !== SOURCE,
    )) {
      expect(surface.entries).toEqual(actual.SURFACE_BY_ID[surface.id].entries);
    }
    const { contract, entries } = await load(() => isolated);
    for (const claimed of [false, true]) {
      const claims = new Set(claimed ? [SOURCE] : []);
      const state = { launch: true, experimentsBasic: false, madeReal: claims };
      expect(splitDrift(contract, entries, state, claims)).toEqual([]);
      expect(
        entries.sanitizeLaunchSearch("?split=1", PAGE, state).changed,
      ).toBe(true);
    }
  });

  it("carries the key on a re-injected owner without mutating its input", async () => {
    const actual = await vi.importActual("src/launch/launchSurfaces");
    const isolated = moduleWithLaneAdditions(actual, []);
    const reinjected = moduleWithLaneAdditions(isolated, [declaration]);
    expect(
      reinjected.SURFACE_BY_ID[TARGET].entries.search.map((rule) => rule.key),
    ).toEqual(["split"]);
    expect(
      reinjected.SURFACE_BY_ID[SOURCE].entries.search.map((rule) => rule.key),
    ).toEqual(["splits"]);
    expect(
      isolated.SURFACE_BY_ID[SOURCE].entries.search
        .map((rule) => rule.key)
        .sort(),
    ).toEqual(["split", "splits"]);
  });

  it.each(["missing", "duplicate", "scope", "values"])(
    "rejects %s ownership drift even while both claims are present",
    async (defect) => {
      const { contract, entries } = await load((actual) => {
        const rule = actual.SURFACE_BY_ID[TARGET].entries.search[0];
        const surfaces = actual.LAUNCH_SURFACES.map((surface) => {
          if (surface.id === SOURCE && defect === "duplicate") {
            return {
              ...surface,
              entries: {
                ...surface.entries,
                search: [...surface.entries.search, rule],
              },
            };
          }
          if (surface.id !== TARGET) return surface;
          const search =
            defect === "missing"
              ? []
              : [
                  {
                    ...rule,
                    ...(defect === "scope"
                      ? { on: /^\/dashboard\/develop\// }
                      : {}),
                    ...(defect === "values" ? { values: ["1"] } : {}),
                  },
                ];
          return { ...surface, entries: { ...surface.entries, search } };
        });
        return withSurfaces(actual, surfaces);
      });
      const claims = new Set([SOURCE, TARGET]);
      expect(
        splitDrift(
          contract,
          entries,
          { launch: true, experimentsBasic: true, madeReal: claims },
          claims,
        ),
      ).toHaveLength(1);
    },
  );
});
