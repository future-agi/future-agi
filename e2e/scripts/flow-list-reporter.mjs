// Used with `playwright test --list`: dumps entries AND collection errors as
// JSON to FLOW_CATALOG_OUT, else stdout. Deterministic input for flow-catalog.mjs.
import { writeFileSync } from "node:fs";

export default class FlowListReporter {
  entries = [];
  errors = [];

  onBegin(config, suite) {
    try {
      this.entries = suite.allTests().map((t) => ({
        title: t.title,
        location: `${t.location.file.split("/e2e/")[1] ?? t.location.file}:${t.location.line}`,
        tags: t.tags,
        annotations: t.annotations,
      }));
    } catch (error) {
      this.onError(error);
    }
    this.writeReport();
  }

  onError(error) {
    this.errors.push({
      message: error?.message || error?.value || String(error),
      stack: error?.stack,
      location: error?.location,
    });
    // Errors can precede or follow onBegin, including when it never runs.
    this.writeReport();
  }

  writeReport() {
    const json = JSON.stringify({ entries: this.entries, errors: this.errors });
    if (process.env.FLOW_CATALOG_OUT)
      writeFileSync(process.env.FLOW_CATALOG_OUT, json);
    return json;
  }

  onEnd() {
    const json = this.writeReport();
    // stdout must be one final JSON document, not lifecycle snapshots.
    if (!process.env.FLOW_CATALOG_OUT) console.log(json);
  }

  printsToStdio() {
    return false;
  }
}
