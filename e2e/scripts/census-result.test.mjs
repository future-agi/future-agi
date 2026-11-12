// Pure W00/J00 receipt checks: no Playwright, installed packages, stack or network.
// Node's built-in type stripping executes the real validator, not a copied oracle.
import assert from "node:assert/strict";
import { registerHooks, stripTypeScriptTypes } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

// The harness uses Bundler-style extensionless imports. Resolve only its local
// walk helpers; type-only imports are erased by Node's built-in transform.
const helpers = new URL("../lib/walk/", import.meta.url).href;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith(".") && context.parentURL?.startsWith(helpers)) {
      const url = new URL(`${specifier}.ts`, context.parentURL);
      if (existsSync(url)) return nextResolve(url.href, context);
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.startsWith(helpers) && url.endsWith(".ts"))
      return {
        format: "module",
        shortCircuit: true,
        source: stripTypeScriptTypes(readFileSync(new URL(url), "utf8"), {
          mode: "strip",
          sourceUrl: url,
        }),
      };
    return nextLoad(url, context);
  },
});
const { censusDigest, verifyCensusResult, CensusResultError } = await import(
  "../lib/walk/census-result.ts"
);
const {
  CENSUS_PHASES,
  CENSUS_ROLES,
  CENSUS_PAGES,
  CENSUS_VIEWPORTS,
  CENSUS_SCHEMES,
  CENSUS_BACKEND_CHECKS,
  CAT2_IDS,
  observeCat2,
} = await import("../lib/walk/census-contract.ts");

const binding = {
  run: censusDigest("synthetic-run"),
  profile: "stock",
  browser: "chromium",
  stack: censusDigest("synthetic-stack"),
  newOrgDefault: false,
};
// ManagedModelsCard.jsx at 3c20e189ea: exact OFF notice; status API list reports
// the organization's effective managed_models.enabled, not deployment availability.
const notice = "Future AGI credits are off for this organization.";
const witness = () => ({
  feature: "managed_models",
  lane: "ui",
  where: "models",
  verdict: "OFF",
  detail: {
    kind: "absent-with-notice",
    control: "ABSENT",
    notice: { present: true, text: notice },
    server: { source: "/agentcc/managed-models/", enabled: false },
  },
});
function fixture() {
  const backend = CENSUS_PHASES.map((phase) => ({
    phase,
    configRows: 0,
    activeVersions: 0,
    mirrorNull: true,
    orgBSnapshot: censusDigest("unchanged"),
    catalogues: {
      api: 0,
      gateway: 0,
      cat2: {
        api: observeCat2([], "api"),
        gateway: observeCat2([], "gateway"),
      },
    },
    verdicts: CENSUS_BACKEND_CHECKS.map(({ lane, feature }) => ({
      lane,
      feature,
      where: phase,
      verdict: "OFF",
      ...(feature === "CAT-2 exact 34 IDs default-off"
        ? { detail: { checked: 34, absent: 34, blockedUnpriced: 0 } }
        : {}),
    })),
  }));
  const ui = CENSUS_PHASES.flatMap((phase) =>
    CENSUS_ROLES.flatMap((role) =>
      CENSUS_PAGES.flatMap((page) =>
        CENSUS_VIEWPORTS.flatMap((viewport) =>
          CENSUS_SCHEMES.map((scheme) => ({
            phase,
            role,
            page,
            viewport,
            scheme,
            verdicts: [
              page === "models"
                ? witness()
                : {
                    feature: "page visited",
                    lane: "ui",
                    where: page,
                    verdict: "RECORDED",
                  },
            ],
          })),
        ),
      ),
    ),
  );
  const credits = CAT2_IDS.map((id) => ({
    id,
    verdict: "refused",
    status: 403,
    code: "permission_denied",
    upstreamBefore: 0,
    upstreamAfter: 0,
  }));
  return {
    schema: "w00-result-v2",
    complete: true,
    binding,
    orgs: { A: censusDigest("A"), B: censusDigest("B") },
    receipt: censusDigest("receipt"),
    backend,
    ui,
    credits,
  };
}
function seal(payload) {
  // Rebuild BOTH inventory and digest: semantic negatives must not fail integrity.
  const verdicts = [
    ...payload.ui.flatMap((r) => r.verdicts),
    ...payload.backend.flatMap((r) => r.verdicts),
    ...payload.credits.map((row) => ({
      lane: "gateway",
      feature: "CAT-2 credits refusal",
      where: row.id,
      verdict: "OFF",
      detail: row,
    })),
  ];
  const body = { ...payload, orgSet: censusDigest(payload.orgs), verdicts };
  return { ...body, digest: censusDigest(body) };
}
function refused(mutate, code = "VERDICT") {
  const payload = fixture();
  mutate(payload, payload.ui[0].verdicts[0]);
  assert.throws(
    () => verifyCensusResult(seal(payload), binding),
    (error) => error instanceof CensusResultError && error.reason === code,
  );
}
test("W00 absent-with-notice is accepted by the J00 v2 reader for all 224 cells", () => {
  const result = verifyCensusResult(seal(fixture()), binding);
  assert.equal(result.schema, "w00-result-v2");
  assert.equal(result.ui.length, 224);
  assert.equal(result.backend.length, 2);
  assert.equal(result.credits.length, 34);
});
for (const state of ["checked", "unchecked", "disabled"])
  test(`W00 refuses a present ${state} switch`, () => {
    refused((_, v) => {
      v.detail.control = "PRESENT";
      v.detail.switchState = state;
    });
  });
test("W00 refuses absence without the positive OFF notice", () =>
  refused((_, v) => {
    v.detail.notice.present = false;
  }));
test("W00 refuses missing notice evidence", () =>
  refused((_, v) => {
    delete v.detail.notice;
  }));
test("W00 refuses a different notice", () =>
  refused((_, v) => {
    v.detail.notice.text = "Could not load credits";
  }));
test("W00 refuses an enabled server readback", () =>
  refused((_, v) => {
    v.detail.server.enabled = true;
  }));
test("W00 refuses missing server readback", () =>
  refused((_, v) => {
    delete v.detail.server;
  }));
test("W00 refuses non-boolean server false", () =>
  refused((_, v) => {
    v.detail.server.enabled = "false";
  }));
test("W00 refuses a non-server source", () =>
  refused((_, v) => {
    v.detail.server.source = "localStorage";
  }));
test("W00 refuses absent-with-notice for another feature", () =>
  refused((_, v) => {
    // Relabel the ONLY Models witness, rather than adding a second row that
    // could fail merely because the cell contains two otherwise valid witnesses.
    v.feature = "mcp";
  }));
test("W00 refuses an additional absent-with-notice for another feature", () =>
  refused((p) => {
    p.ui[1].verdicts.push({ ...witness(), feature: "mcp" });
  }));
for (const state of ["checked", "unchecked", "disabled"])
  test(`W00 refuses a kindless present ${state} control beside a valid witness`, () =>
    refused((p) => {
      p.ui[0].verdicts.push({
        feature: "managed_models",
        lane: "ui",
        where: "models",
        verdict: "RECORDED",
        detail: { control: "PRESENT", switchState: state },
      });
    }));
for (const state of ["checked", "unchecked", "disabled"])
  test(`W00 refuses contradictory ${state} switchState on an absent witness`, () =>
    refused((_, v) => {
      v.detail.switchState = state;
    }));
test("W00 refuses a managed UI row with a missing kind beside a valid witness", () =>
  refused((p) => {
    const row = witness();
    delete row.detail.kind;
    p.ui[0].verdicts.push(row);
  }));
for (const part of ["notice", "server"])
  test(`W00 refuses unknown fields in the ${part} witness`, () =>
    refused((_, v) => {
      v.detail[part].switchState = "checked";
    }));
test("W00 refuses absent-with-notice on another page even for managed_models", () =>
  refused((p) => {
    p.ui.find((r) => r.page === "billing").verdicts.push(witness());
  }));
test("W00 refuses the old switch-only OFF receipt", () =>
  refused((_, v) => {
    v.feature = "managed_models switch";
    delete v.detail;
  }));
test("W00 refuses a present switch alongside an otherwise valid absence witness", () =>
  refused((p) => {
    p.ui[0].verdicts.push({
      feature: "managed_models switch",
      lane: "ui",
      where: "models",
      verdict: "OFF",
    });
  }));
test("W00 refuses the new kind outside the UI inventory", () =>
  refused((p) => {
    p.backend[0].verdicts.push({ ...witness(), lane: "api" });
  }));
test("W00 still refuses incomplete matrix after recomputing digest", () =>
  refused((p) => {
    p.ui.pop();
  }, "PARTIAL"));
