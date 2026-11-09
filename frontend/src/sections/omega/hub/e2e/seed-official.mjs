#!/usr/bin/env node
/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// Seeds the lane registry with the backend's three Official fixtures through
// the real intake and the real checker (A's Hub token, channel cli). They stop
// at in_review; approval is a maintainer's decision, which the e2e run makes
// as maintainer B in the review page.
import path from "node:path";

import { REPO, packFolder, tokenA, upload, waitForStatus } from "./lib/registry.mjs";

const FIXTURES = path.join(REPO, "futureagi/omega_hub/tests/fixtures/entries");
const NAMES = (process.argv[2] || "first-agent,notes-plugin,engineering-team").split(",");
const headers = { "X-Omega-Hub-Token": tokenA() };

const created = [];
for (const name of NAMES) {
  const bundle = packFolder(path.join(FIXTURES, name));
  const row = await upload({ headers, publisher: "future-agi", bundle, name, version: "1.0.0" });
  console.log(`uploaded future-agi/${name}: ${row.id} ${row.status}`);
  created.push({ name, id: row.id });
}
let failed = 0;
for (const { name, id } of created) {
  const done = await waitForStatus(id, headers);
  console.log(`future-agi/${name}: ${done.status} ${JSON.stringify(done.checkSummary?.steps || done.report?.steps?.map((s) => [s.id, s.status]))}`);
  if (done.status !== "in_review") failed += 1;
}
process.exit(failed ? 1 : 0);
