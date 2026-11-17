"use strict";
const identity = (item) => `${item.file}\0${item.title}`;
function reconcileInventory(expected, observed, browser) {
  if (
    !expected.length ||
    expected.length !== observed.length ||
    new Set(observed.map(identity)).size !== observed.length ||
    JSON.stringify(expected.map(identity).sort()) !==
      JSON.stringify(observed.map(identity).sort())
  ) {
    throw new Error("F01 inventory missing, extra, duplicate or zero tests");
  }
  const indexed = new Map(expected.map((item) => [identity(item), item]));
  for (const item of observed) {
    const selected = indexed.get(identity(item));
    if (
      item.browser !== browser ||
      item.project !== "" ||
      item.parentType !== "file" ||
      item.expectedStatus !== "passed"
    ) {
      throw new Error(
        "F01 inventory browser/project/skipped/expected-status drift",
      );
    }
    // All native expected-failure annotations in these specs are set at RUN time.
    // Discovery cannot silently pick up test.skip/fixme or newly labelled rows.
    const types = item.annotations.map((a) => a.type).sort();
    if (
      JSON.stringify(types) !==
      JSON.stringify([...selected.annotationTypes].sort())
    ) {
      throw new Error(
        "F01 annotation inventory drift; fragments never become complete rows",
      );
    }
  }
}
function reconcileTerminal(item, facts) {
  const common =
    facts.starts === 1 &&
    facts.ends === 1 &&
    facts.retry === 0 &&
    facts.fullRowAttachments === 0 &&
    facts.notWalked === false &&
    item.steps.every((step) => facts.stepCounts?.[step] === 1);
  if (
    !common ||
    item.disposition !== "SOURCE_ONLY" ||
    item.kind === "admission-guard"
  ) {
    throw new Error(
      "F01 terminal missing, duplicate, retried, mislabelled or NOT_WALKED",
    );
  }
  if (item.expectedStatus === "failed") {
    if (
      facts.status !== "failed" ||
      facts.expectedStatus !== "failed" ||
      facts.errorCount !== 1 ||
      facts.intendedAssertion !== true
    )
      throw new Error(
        "F01 red terminal is not its intended native assertion failure",
      );
  } else if (
    facts.status !== "passed" ||
    facts.expectedStatus !== "passed" ||
    facts.errorCount !== 0
  ) {
    throw new Error(
      "F01 positive terminal failed, skipped, interrupted or unexpectedly passed",
    );
  }
}
module.exports = { identity, reconcileInventory, reconcileTerminal };
