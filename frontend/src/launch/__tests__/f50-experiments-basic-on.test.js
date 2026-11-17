import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SURFACE_BY_ID } from "../launchSurfaces";
import {
  EXPERIMENTS_BASIC_DEFAULT,
  experimentsBasicFromEnv,
  isSurfaceShown,
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "../launchSwitch";
import { madeRealSurfaceIds } from "../madeReal";
import { launchRedirect } from "../launchRoutes";

// All-on release flip, family "experimentsBasic" (owner Q2;
// walk leg J09.ON.54). Default pins for this family only: the committed
// experimentsBasic default is ON, so a build with VITE_EXPERIMENTS_NEW_PAGES
// unset shows the experiments surfaces; exp.new, dataset.tab.experiments and
// dataset.experimentDefaults ship shown behind the experimentsBasic gate.
// The other family's switches (F50 splits) are asserted only through
// invariants that hold whether or not that family has flipped.

const FAMILY = [
  "exp.new",
  "dataset.tab.experiments",
  "dataset.experimentDefaults",
];
// Followers the committed default reaches too: exp.new.kindMenu and exp.rerun
// by their XP claims, exp.page by its experimentsBasic registry literal.
const FOLLOWERS = ["exp.new.kindMenu", "exp.page", "exp.rerun"];
const defaultState = (madeReal = madeRealSurfaceIds()) => ({
  launch: true,
  experimentsBasic: experimentsBasicFromEnv({}),
  madeReal: new Set(madeReal),
});
const at = (experimentsBasic) => ({
  launch: true,
  experimentsBasic,
  madeReal: madeRealSurfaceIds(),
});

beforeEach(() => vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false"));
afterEach(() => {
  vi.unstubAllEnvs();
  setLaunchOverrideForTest(null);
});
// The switch ships on, so every surface shows (the as-shipped block below).
// The cases that pin what the launch gates hide turn it off in the override
// first, so the gates decide as they do with the switch off.
const switchOff = () => setLaunchOverrideForTest({ showUnreleased: false });

describe("experimentsBasic: committed default ON", () => {
  it("defaults ON when the build input is unset", () => {
    expect(EXPERIMENTS_BASIC_DEFAULT).toBe(true);
    expect(experimentsBasicFromEnv({})).toBe(true);
    expect(experimentsBasicFromEnv()).toBe(true);
    expect(
      experimentsBasicFromEnv({ VITE_EXPERIMENTS_NEW_PAGES: undefined }),
    ).toBe(true);
    // Only the exact literals are build inputs; anything else is unset.
    for (const value of ["", "TRUE", "False", "0", "off"])
      expect(
        experimentsBasicFromEnv({ VITE_EXPERIMENTS_NEW_PAGES: value }),
        JSON.stringify(value),
      ).toBe(true);
    expect(
      experimentsBasicFromEnv({ VITE_EXPERIMENTS_NEW_PAGES: "true" }),
    ).toBe(true);
  });

  it("keeps the existing explicit-off build contract (launchSwitch.js experimentsBasicFromEnv)", () => {
    expect(
      experimentsBasicFromEnv({ VITE_EXPERIMENTS_NEW_PAGES: "false" }),
    ).toBe(false);
  });

  it("ships the family as registry literals shown behind the experimentsBasic gate", () => {
    for (const id of FAMILY) {
      expect(SURFACE_BY_ID[id].ships, id).toBe("shown");
      expect(SURFACE_BY_ID[id].experiments, id).toBe(true);
    }
  });

  it.each([...FAMILY, ...FOLLOWERS])(
    "shows %s in a default launch build",
    (id) => {
      expect(surfaceShownIn(id, defaultState())).toBe(true);
      setLaunchOverrideForTest(defaultState());
      expect(isSurfaceShown(id)).toBe(true);
    },
  );

  it.each(FAMILY)(
    "shows %s from its literal alone, without a lane claim",
    (id) => {
      expect(surfaceShownIn(id, defaultState([]))).toBe(true);
    },
  );

  it.each([...FAMILY, ...FOLLOWERS])(
    "still hides %s when a build explicitly sets the flag false",
    (id) => {
      switchOff();
      const experimentsBasic = experimentsBasicFromEnv({
        VITE_EXPERIMENTS_NEW_PAGES: "false",
      });
      expect(experimentsBasic).toBe(false);
      expect(surfaceShownIn(id, at(experimentsBasic))).toBe(false);
    },
  );

  it.each([undefined, "true", "false"])(
    "offers experiment comparison while other result views keep their gates for %s",
    (value) => {
      switchOff();
      const state = at(
        experimentsBasicFromEnv({ VITE_EXPERIMENTS_NEW_PAGES: value }),
      );
      for (const id of [
        "exp.results.matrix",
        "exp.results.paths",
        "exp.results.traces",
        "exp.results.evalVersionImpact",
        "exp.results.bestOf",
        "exp.results.iterateDock",
      ])
        expect(surfaceShownIn(id, state), id).toBe(false);
      for (const id of ["exp.results.summary", "exp.results.addToDataset"])
        expect(surfaceShownIn(id, state), id).toBe(state.experimentsBasic);
      expect(SURFACE_BY_ID["exp.compare"].ships).toBe("shown");
      expect(surfaceShownIn("exp.compare", state)).toBe(state.experimentsBasic);
      for (const tab of ["matrix", "paths", "traces"])
        expect(
          launchRedirect(
            { pathname: `/dashboard/develop/experiment/e1/${tab}` },
            state,
          ),
          tab,
        ).not.toBeNull();
      // Summary is the pre-existing classic host, NOT DSX Results Summary.
      expect(
        launchRedirect(
          { pathname: "/dashboard/develop/experiment/e1/summary" },
          state,
        ),
      ).toBeNull();
      expect(surfaceShownIn("exp.classic", state)).toBe(true);
    },
  );
});

// The release as shipped: the switch stays on, so with the flag set false the
// family, its followers and the DSX Results surfaces show and their routes
// open. A gate check only; it checks no words.
describe("experimentsBasic: committed default ON as shipped (unreleased parts shown)", () => {
  it("shows the family, its followers and DSX Results with the flag set false", () => {
    const state = at(
      experimentsBasicFromEnv({ VITE_EXPERIMENTS_NEW_PAGES: "false" }),
    );
    const hidden = [
      ...FAMILY,
      ...FOLLOWERS,
      "exp.results.matrix",
      "exp.results.paths",
      "exp.results.traces",
      "exp.compare",
    ].filter((id) => !surfaceShownIn(id, state));
    expect(hidden).toEqual([]);
    for (const tab of ["matrix", "paths", "traces"])
      expect(
        launchRedirect(
          { pathname: `/dashboard/develop/experiment/e1/${tab}` },
          state,
        ),
        tab,
      ).toBeNull();
  });
});

describe("experimentsBasic: only this family's switch lines move", () => {
  it.each(["dataset.splits", "exp.new.splitScope"])(
    "leaves %s independent of experimentsBasic",
    (id) => {
      expect(surfaceShownIn(id, at(true))).toBe(surfaceShownIn(id, at(false)));
    },
  );

  // main 11-09 lifted the 749 hold on these claimed surfaces (EXP-E2E-005,
  // DATA-E2E-021, EVAL-E2E-072); with the flag on they show.
  it.each(["exp.new.scope", "exp.runControl", "evals.dag"])(
    "shows %s with the flag on now that its 749 hold is lifted",
    (id) => {
      expect(surfaceShownIn(id, at(true))).toBe(true);
    },
  );
});

describe("experimentsBasic: no listed build input assigns the flag", () => {
  // The frontend image, the walk stacks and the frontend CI/CD workflows named
  // here (not every file in the repo). A missing file fails: the guard never
  // passes by reading nothing.
  const BUILD_INPUTS = [
    "frontend/Dockerfile",
    "frontend/docker-entrypoint.sh",
    "frontend/vite.config.js",
    "frontend/.env.example",
    "Dockerfile",
    "Dockerfile.oss",
    "docker-compose.yml",
    "docker-compose.frontend.yml",
    "docker-compose.dev.yml",
    "e2e/stack/docker-compose.e2e.yml",
    "e2e/stack/docker-compose.walk-pins.yml",
    "e2e/stack/docker-compose.walk-pins.distributed.yml",
    "e2e/stack/docker-compose.standalone-e2e.yml",
    "e2e/stack/e2e.env",
    "e2e/stack/standalone-e2e.env",
    ".github/workflows/frontend-develop.yml",
    ".github/workflows/frontend-feature.yml",
    ".github/workflows/frontend-main.yml",
    ".github/workflows/frontend-deploy-eu.yaml",
    ".github/workflows/frontend-deploy-us2.yaml",
    ".github/workflows/frontend-dev-cdn.yaml",
    ".github/workflows/release-frontend.yml",
    ".github/workflows/build-image-multiarch.yml",
    ".github/workflows/e2e-ci.yml",
  ];
  // Any assignment other than the literal true is flagged (it could be off).
  const OFF = /VITE_EXPERIMENTS_NEW_PAGES["']?\s*[:=](?!\s*["']?true\b)/m;
  const read = (p) => readFileSync(resolve(process.cwd(), "..", p), "utf8");

  it("flags every non-true assignment (positive controls)", () => {
    for (const text of [
      "VITE_EXPERIMENTS_NEW_PAGES=false",
      "ARG VITE_EXPERIMENTS_NEW_PAGES=false",
      "--build-arg VITE_EXPERIMENTS_NEW_PAGES=false",
      '  VITE_EXPERIMENTS_NEW_PAGES: "false"',
      "VITE_EXPERIMENTS_NEW_PAGES: 'false'",
      "VITE_EXPERIMENTS_NEW_PAGES=",
      "VITE_EXPERIMENTS_NEW_PAGES=${FLAG}",
    ])
      expect(OFF.test(text), text).toBe(true);
    for (const text of [
      "VITE_EXPERIMENTS_NEW_PAGES=true",
      '  VITE_EXPERIMENTS_NEW_PAGES: "true"',
      "VITE_HOST_API=http://localhost:8000",
    ])
      expect(OFF.test(text), text).toBe(false);
  });

  it.each(BUILD_INPUTS)(
    "%s assigns VITE_EXPERIMENTS_NEW_PAGES nothing but the literal true",
    (p) => {
      const text = read(p);
      expect(text.length, p).toBeGreaterThan(0);
      expect(OFF.test(text), p).toBe(false);
    },
  );
});
