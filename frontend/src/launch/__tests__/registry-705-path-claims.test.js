import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as real from "../madeReal";
import { SURFACE_BY_ID } from "../launchSurfaces";
import prototypePaths from "../__fixtures__/registry-prototype-paths-705.json";
import { claimViolations } from "./registryClaims";
import {
  flowsWithWalks,
  moduleWithClaims,
  sourceWithClaims,
} from "./registryClaimSets";

const SOURCE = readFileSync(
  resolve(process.cwd(), "src/launch/madeReal.js"),
  "utf8",
);
const FLOWS = readFileSync(resolve(process.cwd(), "../e2e/FLOWS.md"), "utf8");
const PATH = "/model-hub/datasets/{dataset_id}/splits/";
const COMMENT = "FIXTURE-E2E-003: two-organization splits.";
const setOf = (path = PATH, slot = "GRID", id = "dataset.splits") => ({
  claims: { [slot]: id ? [{ id, comment: COMMENT }] : [] },
  paths: { [slot]: [{ id: path, comment: COMMENT }] },
  flows: ["### FIXTURE-E2E-003 - fixture only"],
});
const validate = (set, check = claimViolations) => {
  const module = moduleWithClaims(real, set);
  return check({
    MADE_REAL: module.MADE_REAL,
    MADE_REAL_PATHS: module.MADE_REAL_PATHS,
    source: sourceWithClaims(SOURCE, set),
    flows: flowsWithWalks(FLOWS, set),
    live: module.madeRealSurfaceIds(),
  });
};
const DSX_REASON = "dsx prototype (PRODUCT-SPEC §6.2)";
/** Fresh registry modules over a changed copy of the live exceptions. */
async function withLiveExceptions(change) {
  vi.resetModules();
  vi.doMock(
    "src/api/contracts/api-contract-exceptions",
    async (importOriginal) => {
      const original = await importOriginal();
      const live = { ...original.API_CONTRACT_EXCEPTIONS };
      change(live);
      return { ...original, API_CONTRACT_EXCEPTIONS: live };
    },
  );
  const guard = await import("../absentPathGuard");
  const { claimViolations } = await import("./registryClaims");
  return {
    absent: guard.absentTemplates(undefined, new Set()),
    claimViolations,
  };
}
const invalid = [
  ["unknown path", setOf("/model-hub/not-a-path/"), ["unknown-path"]],
  [
    "contracted path never in P0",
    setOf("/model-hub/develops/get-datasets/"),
    ["unknown-path"],
  ],
  ["no surface claim", setOf(PATH, "GRID", null), ["path-without-claim"]],
  [
    "unrelated surface in the same slot",
    setOf(PATH, "GRID", "dataset.export"),
    ["path-without-surface"],
  ],
  [
    "another slot with its own valid claim",
    setOf(PATH, "ORG", "list.organize.query"),
    ["wrong-path-slot", "path-without-surface"],
  ],
  [
    "matching surface in the wrong slot",
    setOf(PATH, "ORG"),
    ["wrong-slot", "wrong-path-slot"],
  ],
];

afterEach(() => {
  vi.doUnmock("src/api/contracts/api-contract-exceptions");
  vi.resetModules();
});

describe("P0 prototype path claim provenance", () => {
  it("pins all 87 templates with registered backing surfaces", () => {
    expect(Object.keys(prototypePaths.owners)).toHaveLength(87);
    for (const owners of Object.values(prototypePaths.owners)) {
      expect(owners.length).toBeGreaterThan(0);
      expect(new Set(owners).size).toBe(owners.length);
      for (const id of owners) expect(SURFACE_BY_ID[id], id).toBeDefined();
    }
  });

  it.each([PATH, `${PATH}{split_id}/`])(
    "accepts the backed split path %s",
    (path) => {
      expect(validate(setOf(path))).toEqual([]);
    },
  );

  // registryClaims.js reads the pinned fixture, never the live exceptions.
  // Each case mocks the live exceptions, proves the launch guard (which does
  // read them) saw the change, and checks claims against the pinned P0 set.
  it("keeps contracted P0 paths claimable once the live exceptions drop them", async () => {
    const contracted = [PATH, `${PATH}{split_id}/`];
    const fresh = await withLiveExceptions((live) => {
      for (const path of contracted) delete live[path];
    });
    for (const path of contracted) {
      expect(fresh.absent, path).not.toContain(path);
      expect(prototypePaths.owners[path], path).toEqual(["dataset.splits"]);
      expect(validate(setOf(path), fresh.claimViolations), path).toEqual([]);
      expect(
        validate(setOf(path, "GRID", null), fresh.claimViolations).map(
          (v) => v.code,
        ),
      ).toEqual(["path-without-claim"]);
      expect(
        validate(
          setOf(path, "GRID", "dataset.export"),
          fresh.claimViolations,
        ).map((v) => v.code),
      ).toEqual(["path-without-surface"]);
    }
  });

  it("rejects a path outside the pinned P0 set even when a live exception lists it", async () => {
    const path = "/model-hub/datasets/{dataset_id}/new-prototype/";
    const fresh = await withLiveExceptions((live) => {
      live[path] = Object.values(live).find(
        (meta) => meta.reason === DSX_REASON,
      );
    });
    expect(fresh.absent).toContain(path);
    expect(Object.hasOwn(prototypePaths.owners, path)).toBe(false);
    expect(
      validate(setOf(path), fresh.claimViolations).map((v) => v.code),
    ).toEqual(["unknown-path"]);
  });

  it.each(invalid)("rejects %s", (_name, set, codes) => {
    expect(validate(set).map((v) => v.code)).toEqual(codes);
  });

  it("does not borrow a backing claim from another slot", () => {
    const set = setOf(PATH, "ORG", "list.organize.query");
    set.claims.GRID = [{ id: "dataset.splits", comment: COMMENT }];
    expect(validate(set).map((v) => v.code)).toEqual([
      "wrong-path-slot",
      "path-without-surface",
    ]);
  });

  it("still detects duplicate paths across slots", () => {
    const set = setOf();
    set.claims.ORG = [{ id: "list.organize.query", comment: COMMENT }];
    set.paths.ORG = [{ id: PATH, comment: COMMENT }];
    expect(validate(set).map((v) => v.code)).toContain("duplicate-path");
  });
});
