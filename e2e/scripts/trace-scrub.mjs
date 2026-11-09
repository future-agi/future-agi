#!/usr/bin/env node
// 708 add.78: the trace POST-PROCESSOR as a command, for traces written before
// the capture-time hook existed or by a runner that bypassed it:
//   node e2e/scripts/trace-scrub.mjs [--force] <file-or-dir>...
// Every Playwright trace archive (*.zip) under the paths, and each named file,
// is rewritten in place by e2e/lib/trace-hygiene/archive.mjs: test.trace, the
// context trace and network files, stacks and network resources. Archives that
// already carry trace-hygiene.json are skipped unless --force. Scrubbing is not
// the gate: run e2e/scripts/evidence-audit.mjs before the evidence lands.
// Exit 0: done; 1: an archive could not be rewritten (left as is); 2: usage.
import { sweepTraceArchives } from "../lib/trace-hygiene/archive.mjs";

const args = process.argv.slice(2);
const force = args.includes("--force");
const paths = args.filter((a) => a !== "--force");
if (!paths.length) {
  console.error("usage: trace-scrub.mjs [--force] <file-or-dir>...");
  process.exit(2);
}
const { scrubbed, failed } = sweepTraceArchives(paths, { force });
console.log(`trace-scrub: scrubbed=${scrubbed.length} failed=${failed.length}`);
for (const f of failed) console.log(`failed: ${f}`);
process.exit(failed.length ? 1 : 0);
