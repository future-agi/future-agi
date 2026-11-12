// Safe-to-pull numeric Playwright summary. Never copy titles, errors, stdout,
// response bodies or attachments: even synthetic fixture credentials stay on VM.
const { mkdirSync, writeFileSync } = require("node:fs");
const path = require("node:path");
const statuses = ["passed", "failed", "timedOut", "skipped", "interrupted"];
module.exports = class CountsReporter {
  constructor(options = {}) {
    this.outputFile = options.outputFile || process.env.E2E_WALK_COUNTS;
    if (!this.outputFile)
      throw new Error("counts reporter needs outputFile or E2E_WALK_COUNTS");
    this.failures = [];
    this.counts = {
      planned: 0,
      completed: 0,
      passed: 0,
      failed: 0,
      timedOut: 0,
      skipped: 0,
      interrupted: 0,
      errors: 0,
    };
  }
  onBegin(_config, suite) {
    this.counts.planned = suite.allTests().length;
  }
  onTestEnd(_test, result) {
    if (statuses.includes(result.status)) {
      this.counts[result.status]++;
      this.counts.completed++;
    }
    if (["failed", "timedOut", "interrupted"].includes(result.status)) {
      // Only source locations survive. Do not copy messages, diffs, URLs,
      // source snippets or arbitrary stack lines into a pullable receipt.
      const frames = (result.errors || []).flatMap((error) =>
        [
          ...String(error.stack || "").matchAll(
            /\/(e2e\/(?:lib|flows|harness)\/[A-Za-z0-9_./-]+):(\d+):(\d+)/g,
          ),
        ].map((match) => ({
          file: match[1],
          line: Number(match[2]),
          column: Number(match[3]),
        })),
      );
      const unique = [
        ...new Map(
          frames.map((frame) => [JSON.stringify(frame), frame]),
        ).values(),
      ].slice(0, 8);
      if (unique.length)
        this.failures.push({ status: result.status, frames: unique });
    }
  }
  onError() {
    this.counts.errors++;
  }
  onEnd(result) {
    mkdirSync(path.dirname(this.outputFile), { recursive: true });
    writeFileSync(
      this.outputFile,
      JSON.stringify(
        {
          ...this.counts,
          ...(this.failures.length ? { failures: this.failures } : {}),
          status: ["passed", "failed", "timedout", "interrupted"].includes(
            result.status,
          )
            ? result.status
            : "unknown",
          durationMs: Number.isFinite(result.duration) ? result.duration : 0,
        },
        null,
        2,
      ) + "\n",
    );
  }
};
