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
// experimentsBasic default is ON, so a build with VITE_DSX_EXPERIMENTS_BASIC
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

describe("experimentsBasic: committed default ON", () => {
  it("defaults ON when the build input is unset", () => {
    expect(EXPERIMENTS_BASIC_DEFAULT).toBe(true);
    expect(experimentsBasicFromEnv({})).toBe(true);
    expect(experimentsBasicFromEnv()).toBe(true);
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: undefined }),
    ).toBe(true);
    // Only the exact literals are build inputs; anything else is unset.
    for (const value of ["", "TRUE", "False", "0", "off"])
      expect(
        experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: value }),
        JSON.stringify(value),
      ).toBe(true);
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: "true" }),
    ).toBe(true);
  });

  it("keeps the existing explicit-off build contract (launchSwitch.js experimentsBasicFromEnv)", () => {
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: "false" }),
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
      const experimentsBasic = experimentsBasicFromEnv({
        VITE_DSX_EXPERIMENTS_BASIC: "false",
      });
      expect(experimentsBasic).toBe(false);
      expect(surfaceShownIn(id, at(experimentsBasic))).toBe(false);
    },
  );

  it.each([undefined, "true", "false"])(
    "keeps every DSX Results surface and route closed for build input %s",
    (value) => {
      const state = at(
        experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: value }),
      );
      for (const id of [
        "exp.results.matrix",
        "exp.results.paths",
        "exp.results.summary",
        "exp.results.traces",
        "exp.results.evalVersionImpact",
        "exp.results.bestOf",
        "exp.results.addToDataset",
        "exp.results.iterateDock",
        "exp.compare",
      ])
        expect(surfaceShownIn(id, state), id).toBe(false);
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
    ".github/workflows/frontend-prod-deploy.yaml",
    ".github/workflows/frontend-deploy-eu.yaml",
    ".github/workflows/frontend-deploy-us2.yaml",
    ".github/workflows/frontend-dev-cdn.yaml",
    ".github/workflows/release-frontend.yml",
    ".github/workflows/build-image-multiarch.yml",
    ".github/workflows/e2e-ci.yml",
  ];
  // Any assignment other than the literal true is flagged (it could be off).
  const OFF = /VITE_DSX_EXPERIMENTS_BASIC["']?\s*[:=](?!\s*["']?true\b)/m;
  const read = (p) => readFileSync(resolve(process.cwd(), "..", p), "utf8");

  it("flags every non-true assignment (positive controls)", () => {
    for (const text of [
      "VITE_DSX_EXPERIMENTS_BASIC=false",
      "ARG VITE_DSX_EXPERIMENTS_BASIC=false",
      "--build-arg VITE_DSX_EXPERIMENTS_BASIC=false",
      '  VITE_DSX_EXPERIMENTS_BASIC: "false"',
      "VITE_DSX_EXPERIMENTS_BASIC: 'false'",
      "VITE_DSX_EXPERIMENTS_BASIC=",
      "VITE_DSX_EXPERIMENTS_BASIC=${FLAG}",
    ])
      expect(OFF.test(text), text).toBe(true);
    for (const text of [
      "VITE_DSX_EXPERIMENTS_BASIC=true",
      '  VITE_DSX_EXPERIMENTS_BASIC: "true"',
      "VITE_HOST_API=http://localhost:8000",
    ])
      expect(OFF.test(text), text).toBe(false);
  });

  it.each(BUILD_INPUTS)(
    "%s assigns VITE_DSX_EXPERIMENTS_BASIC nothing but the literal true",
    (p) => {
      const text = read(p);
      expect(text.length, p).toBeGreaterThan(0);
      expect(OFF.test(text), p).toBe(false);
    },
  );
});
