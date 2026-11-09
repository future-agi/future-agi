/* eslint-env node */
import fs from "node:fs";
import { pathToFileURL } from "node:url";

export const annotationFacade =
  "src/components/traceDetailDrawer/AnnotateDrawer.jsx";

/** Compare measured, deduplicated entry + facade static JS/CSS transfers. */
export function checkRouteTransferBudget(baseline, integrated, candidate) {
  const failures = [];
  const indexed = (report) =>
    new Map(report.routes.map((row) => [row.facade, row.staticTransfer]));
  const old = indexed(baseline);
  const accepted = indexed(integrated);
  const next = indexed(candidate);
  for (const [facade, reference] of accepted) {
    const limit = facade === annotationFacade ? 1.05 : 1.02;
    const before = facade === annotationFacade ? old.get(facade) : reference;
    const after = next.get(facade);
    if (
      !Number.isFinite(before?.gzip) ||
      !Number.isFinite(after?.gzip) ||
      before.gzip <= 0 ||
      after.gzip <= 0 ||
      after.gzip > before.gzip * limit
    ) {
      failures.push({ facade, before, after, limit });
    }
  }
  if (!accepted.has(annotationFacade) || !old.has(annotationFacade)) {
    failures.push({ facade: annotationFacade, reason: "Missing reference" });
  }
  return { passed: failures.length === 0, failures };
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const [baselineFile, integratedFile, candidateFile] = process.argv.slice(2);
  const read = (file) => JSON.parse(fs.readFileSync(file, "utf8"));
  const verdict = checkRouteTransferBudget(
    read(baselineFile),
    read(integratedFile),
    read(candidateFile),
  );
  console.log(JSON.stringify(verdict, null, 2));
  process.exitCode = verdict.passed ? 0 : 1;
}
