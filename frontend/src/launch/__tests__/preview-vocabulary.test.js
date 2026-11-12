import { afterEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "../launchSwitch";
import { madeRealSurfaceIds } from "../madeReal";
import { LAUNCH_SURFACES } from "../launchSurfaces";
import { launchRedirect } from "../launchRoutes";
import { absentTemplateOf, installAbsentPathGuard } from "../absentPathGuard";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import { experimentSurfaceOf } from "src/sections/experiment-detail-classic/experimentsBasic";
import {
  FRAMEWORKS,
  HARNESSES,
} from "src/sections/common/RunSources/catalogEntries";
import {
  allEntries,
  catalogEntry,
  cardsForKind,
  entriesFor,
  isOffered,
  searchCatalog,
} from "src/sections/common/RunSources/catalog";
import { searchGlossary } from "src/sections/common/ux/glossary";

// Launch-build tests run with the show-unreleased switch off, in the override
// (launchSwitch reads it) and in the export the product modules read. The
// tests "as shipped" leave showUnreleased out of the override and turn the
// export on, as the release build has them.
const unreleased = vi.hoisted(() => ({ shown: false }));
vi.mock("../launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

afterEach(() => {
  setLaunchOverrideForTest(null);
  unreleased.shown = false;
});

const hidden = [
  "models.decision",
  "runners.connect",
  "dataset.versions",
  "dataset.rowExtras.runs",
  "dataset.collab.comments",
  "exp.results.matrix",
  "exp.compare",
];

describe("preview vocabulary does not offer an integration", () => {
  it.each([false, true])(
    "keeps unsupported surfaces hidden with actual capability claims (basic=%s)",
    (experimentsBasic) => {
      const state = {
        launch: true,
        showUnreleased: false,
        experimentsBasic,
        madeReal: madeRealSurfaceIds(),
      };
      setLaunchOverrideForTest(state);
      for (const id of hidden) {
        expect(isSurfaceShown(id), id).toBe(false);
        expect(surfaceShownIn(id, { ...state, launch: false }), id).toBe(true);
      }
      for (const id of ["exp.results.matrix", "exp.compare"]) {
        const surface = LAUNCH_SURFACES.find((entry) => entry.id === id);
        for (const route of surface.entries.routes) {
          expect(
            launchRedirect({ pathname: route.sample }, state),
          ).not.toBeNull();
        }
      }
      expect(
        experimentSurfaceOf({ prototype: false, basic: experimentsBasic }),
      ).toBe("classic");
      expect(
        experimentSurfaceOf({ prototype: true, basic: experimentsBasic }),
      ).toBe("dsx");
    },
  );

  it.each([false, true])(
    "as shipped, shows the same surfaces and opens their pages (basic=%s)",
    (experimentsBasic) => {
      unreleased.shown = true;
      const state = {
        launch: true,
        experimentsBasic,
        madeReal: madeRealSurfaceIds(),
      };
      setLaunchOverrideForTest(state);
      for (const id of hidden) {
        expect(isSurfaceShown(id), id).toBe(true);
      }
      for (const id of ["exp.results.matrix", "exp.compare"]) {
        const surface = LAUNCH_SURFACES.find((entry) => entry.id === id);
        for (const route of surface.entries.routes) {
          expect(launchRedirect({ pathname: route.sample }, state)).toBeNull();
        }
      }
      expect(
        experimentSurfaceOf({
          prototype: false,
          basic: experimentsBasic,
          showUnreleased: true,
        }),
      ).toBe("dsx");
    },
  );

  it("keeps harness and framework identity for old data without offering creation or search", () => {
    setLaunchOverrideForTest({ launch: false });
    const offered = new Set(allEntries().map((entry) => entry.id));
    for (const entry of [...FRAMEWORKS, ...HARNESSES]) {
      expect(catalogEntry(entry.id)).toBe(entry);
      expect(isOffered(entry.id)).toBe(false);
      expect(offered.has(entry.id)).toBe(false);
      expect(searchCatalog(entry.name).some((hit) => hit.id === entry.id)).toBe(
        false,
      );
    }
    expect(entriesFor("harness")).toEqual([]);
    expect(entriesFor("code")).toEqual([]);
    for (const kind of ["rows", "task_suite", "conversations", "scenarios"]) {
      expect(
        cardsForKind(kind).some((card) =>
          ["harness", "code"].includes(card.id),
        ),
      ).toBe(false);
    }
    expect(searchGlossary("harness").some(({ key }) => key === "harness")).toBe(
      false,
    );
  });

  it("keeps organization and version fixtures unavailable on the real dataset API", () => {
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      madeReal: [],
    });
    for (const key of [
      "listFolders",
      "listOrganize",
      "saveVersion",
      "pendingChanges",
      "selectionVersionedActions",
    ]) {
      expect(datasetCaps(true)[key], key).toBe(false);
      expect(datasetCaps(false)[key], key).toBe(true);
    }
  });

  it("as shipped, turns on folders and organizing on the real dataset API and keeps version fixtures off", () => {
    unreleased.shown = true;
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    for (const key of ["listFolders", "listOrganize"]) {
      expect(datasetCaps(true)[key], key).toBe(true);
    }
    for (const key of [
      "saveVersion",
      "pendingChanges",
      "selectionVersionedActions",
    ]) {
      expect(datasetCaps(true)[key], key).toBe(false);
      expect(datasetCaps(false)[key], key).toBe(true);
    }
  });

  it("refuses decision listings, runner connections and run data before a network call", () => {
    for (const url of [
      "/agentcc/decisions/models/",
      "/model-hub/r4/connect/machines/",
      "/model-hub/datasets/d1/runs/",
    ]) {
      expect(absentTemplateOf(url), url).not.toBeNull();
    }
  });

  // The refusal above applies only where the guard is installed: a launch
  // build with the switch off. As shipped the guard is not installed, so the
  // decision listing, which the backend serves, is requested.
  it("as shipped, requests the decision listing: the guard is not installed", () => {
    unreleased.shown = true;
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: [],
    });
    const use = vi.fn();
    const installed = installAbsentPathGuard({
      interceptors: { request: { use } },
    });
    expect(installed).toBe(false);
    expect(use).not.toHaveBeenCalled();
    expect(isSurfaceShown("models.decision")).toBe(true);
  });
});
