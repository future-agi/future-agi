import assert from "node:assert/strict";
import { test } from "node:test";
import {
  annotationFacade,
  checkRouteTransferBudget,
} from "./check-route-transfer-budget.mjs";

const report = (annotation, other = 1000) => ({
  routes: [
    { facade: annotationFacade, staticTransfer: { gzip: annotation } },
    { facade: "src/pages/other.jsx", staticTransfer: { gzip: other } },
  ],
});

test("drawer uses the pre-split baseline, not the regressed integration", () => {
  assert.equal(
    checkRouteTransferBudget(report(1000), report(1800), report(1800)).passed,
    false,
  );
  assert.equal(
    checkRouteTransferBudget(report(1000), report(1800), report(1050)).passed,
    true,
  );
});

test("every other facade has a two-percent integrated budget", () => {
  assert.equal(
    checkRouteTransferBudget(report(1000), report(1800), report(1000, 1020))
      .passed,
    true,
  );
  assert.equal(
    checkRouteTransferBudget(report(1000), report(1800), report(1000, 1021))
      .passed,
    false,
  );
});

test("missing routes, invalid measurements and missing references fail closed", () => {
  for (const candidate of [
    { routes: [] },
    report(null),
    report(0),
    report(1000, NaN),
  ]) {
    assert.equal(
      checkRouteTransferBudget(report(1000), report(1800), candidate).passed,
      false,
    );
  }
  assert.equal(
    checkRouteTransferBudget({ routes: [] }, report(1800), report(1000)).passed,
    false,
  );
});
