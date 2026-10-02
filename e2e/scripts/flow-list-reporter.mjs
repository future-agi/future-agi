// Used with `playwright test --list`: dumps every test with tags+annotations as
// JSON to FLOW_CATALOG_OUT, else stdout. Deterministic input for flow-catalog.mjs.
import { writeFileSync } from "node:fs";

export default class FlowListReporter {
  onBegin(config, suite) {
    const entries = suite.allTests().map((t) => ({
      title: t.title,
      location: `${t.location.file.split("/e2e/")[1] ?? t.location.file}:${t.location.line}`,
      tags: t.tags,
      annotations: t.annotations,
    }));
    const json = JSON.stringify(entries);
    if (process.env.FLOW_CATALOG_OUT)
      writeFileSync(process.env.FLOW_CATALOG_OUT, json);
    else console.log(json);
  }
  printsToStdio() {
    return false;
  }
}
