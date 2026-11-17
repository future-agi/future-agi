import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { absentTemplateOf } from "../absentPathGuard";
import { SURFACE_BY_ID } from "../launchSurfaces";
import { setLaunchOverrideForTest, surfaceShownIn } from "../launchSwitch";
import * as madeReal from "../madeReal";
import { blockEntries, claimViolations } from "./registryClaims";
import { moduleWithClaims, sourceWithClaims } from "./registryClaimSets";

const read = (path) =>
  readFileSync(resolve(process.cwd(), "src", path), "utf8");
const source = read("launch/madeReal.js");
const flows = readFileSync(
  resolve(process.cwd(), "..", "e2e", "FLOWS.md"),
  "utf8",
);
const WALKED = [
  ["RES", "exp.results.addToDataset", "DSX-WALK-RES-21", true],
  ["RES", "exp.results.summary", "DSX-WALK-RES-51", true],
  ["IN", "dataset.addBlankRow", "DSX-WALK-IN-01", false],
  ["IN", "dataset.imports.blankGrid", "DSX-WALK-IN-02", false],
  ["IN", "dataset.upload", "DSX-WALK-IN-04", false],
  ["ORG", "dataset.duplicateMove", "DSX-WALK-ORG-51", false],
  ["ORG", "dataset.list.columns", "DSX-WALK-ORG-52", false],
  ["ORG", "dataset.switcher", "DSX-WALK-ORG-53", false],
  ["ORG", "list.organize.star", "DSX-WALK-ORG-55", false],
  ["EVAL", "dataset.evalVersionNotice", "DSX-WALK-EVAL-02", false],
  ["EVAL", "dataset.evalVersions", "DSX-WALK-EVAL-03", false],
  ["EVAL", "dataset.experimentDefaults", "DSX-WALK-EVAL-04", true],
  ["EVAL", "evals.createOutputType", "DSX-WALK-EVAL-06", false],
  ["EVAL", "evals.groups", "DSX-WALK-EVAL-07", false],
  ["EVAL", "exp.new.evalCardChips", "DSX-WALK-EVAL-08", true],
  ["EVAL", "evals.agreement", "DSX-WALK-EVAL-81", false],
  ["EVAL", "exp.new.readsTable", "DSX-WALK-EVAL-84", true],
  ["EVAL", "exp.new.savedInputReads", "DSX-WALK-EVAL-85", true],
  ["EVAL", "palette.glossary", "DSX-WALK-EVAL-87", false],
  ["EVAL", "shell.notFound.page", "DSX-WALK-EVAL-88", false],
  ["SRC", "dataset.sdkRunTraces", "DSX-WALK-SRC-01", false],
];
const state = (experimentsBasic) => ({
  launch: true,
  experimentsBasic,
  madeReal: madeReal.madeRealSurfaceIds(),
});

beforeEach(() => setLaunchOverrideForTest({ showUnreleased: false }));
afterEach(() => setLaunchOverrideForTest(null));

describe("registered launch walk claims", () => {
  it("claims each walked owner once in its assigned slot with registered provenance", () => {
    for (const [slot, id, walk] of WALKED) {
      expect(SURFACE_BY_ID[id].madeRealBy, id).toBe(slot);
      expect(
        Object.entries(madeReal.MADE_REAL).flatMap(([key, ids]) =>
          ids.filter((value) => value === id).map(() => key),
        ),
        id,
      ).toEqual([slot]);
      expect(
        blockEntries(source, slot, "claims").filter(
          (entry) => entry.value === id,
        ),
        id,
      ).toEqual([{ value: id, walk }]);
      expect(source, id).toContain(`"${id}", // ${walk}`);
      expect(flows, id).toContain(`### ${walk} `);
    }
    expect(
      claimViolations({
        ...madeReal,
        source,
        flows,
        live: madeReal.madeRealSurfaceIds(),
      }),
    ).toEqual([]);
  });

  it("shows the live claims while retaining the experiment flag for wizard surfaces", () => {
    for (const basic of [false, true]) {
      for (const [, id, , requiresBasic] of WALKED)
        expect(surfaceShownIn(id, state(basic)), `${id} basic=${basic}`).toBe(
          !requiresBasic || basic,
        );
    }
  });

  it("keeps activity and unsupported Results owners unclaimed with their paths refused", () => {
    for (const id of [
      "dataset.rowTab.activity",
      "dataset.rowExtras.activity",
      "exp.results.matrix",
      "exp.results.iterateDock",
    ]) {
      expect(madeReal.madeRealSurfaceIds().has(id), id).toBe(false);
      expect(surfaceShownIn(id, state(true)), id).toBe(false);
    }
    for (const template of [
      "/model-hub/datasets/{dataset_id}/rows/{row_id}/activity/",
      "/model-hub/datasets/{dataset_id}/baseline/",
      "/model-hub/datasets/{dataset_id}/derived-metrics/",
      "/model-hub/datasets/{dataset_id}/classification/",
      "/model-hub/experiments/v2/{experiment_id}/runs/",
    ]) {
      expect(madeReal.madeRealPathTemplates().has(template), template).toBe(
        false,
      );
      expect(
        absentTemplateOf(template.replace(/\{[^}]+\}/g, "fixture-id")),
      ).toBe(template);
    }
  });

  // EVAL-E2E-083 (main ruling, t37): judge improvement needs a managed LLM
  // route no stack activates yet, so it stays unclaimed and hidden; stored
  // agreement stays claimed and shown.
  it("shows stored agreement and hides the unclaimed judge improvement", () => {
    for (const basic of [false, true]) {
      expect(surfaceShownIn("evals.agreement", state(basic))).toBe(true);
      expect(surfaceShownIn("evals.agreementTools", state(basic))).toBe(false);
    }
    expect(madeReal.madeRealSurfaceIds().has("evals.agreementTools")).toBe(
      false,
    );
    const agreement = read("sections/evals/alignment/AgreementTab.jsx");
    expect(agreement).toContain(
      [
        "const AgreementTab = (props) =>",
        '  isSurfaceShown("evals.agreementTools") ? (',
        "    <AdvancedAgreementTab {...props} />",
        "  ) : (",
        "    <StoredAgreement key={props.templateId} templateId={props.templateId} />",
        "  );",
      ].join("\n"),
    );
    expect(agreement).toContain(
      "mode={data?.improvement?.mode ?? data?.improve_mode ?? null}",
    );
    expect(read("sections/evals/alignment/ImproveJudgePanel.jsx")).toContain(
      "  if (!mode) return null;\n  if (!target)",
    );
    expect(
      absentTemplateOf("/model-hub/eval-templates/template-id/agreement/"),
    ).toBeNull();
    // Improve stays refused until its walk passes (main ruling t37).
    const improve = "/model-hub/eval-templates/{template_id}/improve/";
    expect(madeReal.madeRealPathTemplates().has(improve)).toBe(false);
    expect(
      absentTemplateOf("/model-hub/eval-templates/template-id/improve/"),
    ).toBe(improve);
  });
});

describe("accepted Results claim boundaries", () => {
  it.each(["exp.results.summary", "exp.results.addToDataset"])(
    "still rejects %s without its accepted Results walk",
    (id) => {
      const claims = { claims: { RES: [{ id, comment: "DATA-E2E-003" }] } };
      expect(
        claimViolations({
          ...moduleWithClaims(madeReal, claims),
          source: sourceWithClaims(source, claims),
          flows,
        }),
      ).toEqual([{ code: "results-id", slot: "RES", value: id }]);
    },
  );

  it("claims exactly the accepted Results reads and copy templates", () => {
    const entries = blockEntries(source, "RES", "paths");
    expect(entries).toEqual([
      {
        value: "/model-hub/datasets/{dataset_id}/rows/from-results/",
        walk: "DSX-WALK-RES-21",
      },
      {
        value: "/model-hub/datasets/new/rows/from-results/",
        walk: "DSX-WALK-RES-21",
      },
      {
        value: "/model-hub/datasets/{dataset_id}/results/",
        walk: "DSX-WALK-RES-21",
      },
      {
        value: "/model-hub/datasets/{dataset_id}/results/analysis/",
        walk: "DSX-WALK-RES-51",
      },
      {
        value: "/model-hub/datasets/{dataset_id}/runs/",
        walk: "DSX-WALK-RES-51",
      },
      {
        value: "/model-hub/datasets/{dataset_id}/rows/{row_id}/runs/",
        walk: "DATA-E2E-179",
      },
    ]);
    for (const { value } of entries)
      expect(
        absentTemplateOf(value.replace(/\{[^}]+\}/g, "fixture-id")),
      ).toBeNull();
  });
});

const violationsForWalk = (walk, registered = `### ${walk} — registered`) => {
  const claims = {
    claims: { IN: [{ id: "dataset.upload", comment: walk }] },
  };
  return claimViolations({
    ...moduleWithClaims(madeReal, claims),
    source: sourceWithClaims(source, claims),
    flows: registered,
  });
};

describe("registered walk provenance grammar", () => {
  it("accepts the existing walk format and both identifier widths in every permitted area", () => {
    expect(violationsForWalk("DATA-E2E-003")).toEqual([]);
    for (const area of [
      "IN",
      "ORG",
      "EVAL",
      "COLLAB",
      "SRC",
      "RES",
      "GRID",
      "XP",
    ])
      for (const number of ["01", "123"])
        expect(violationsForWalk(`DSX-WALK-${area}-${number}`)).toEqual([]);
  });

  it("rejects unknown areas and identifiers outside the permitted widths", () => {
    for (const walk of [
      "DSX-WALK-OTHER-01",
      "DSX-WALK-in-01",
      "DSX-WALK-IN-1",
      "DSX-WALK-IN-0001",
    ])
      expect(violationsForWalk(walk), walk).toEqual([
        { code: "missing-walk", slot: "IN", value: "dataset.upload" },
      ]);
  });

  it("requires the claimed walk to appear as a registered heading", () => {
    const walk = "DSX-WALK-IN-04";
    for (const registered of [
      "",
      `Mentioned only in prose: ${walk}`,
      "### DSX-WALK-IN-040 — a different walk",
    ])
      expect(violationsForWalk(walk, registered)).toEqual([
        {
          code: "unknown-walk",
          slot: "IN",
          value: "dataset.upload",
          detail: walk,
        },
      ]);
  });
});
