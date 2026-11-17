// Cloud-only checks of the journey adoption boundary; no live stack or actors.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

const exec = promisify(execFile);
const root = fileURLToPath(new URL("../", import.meta.url));
const cli = join(root, "node_modules/@playwright/test/cli.js");
const collect = (suites = []) =>
  suites.flatMap((suite) => [
    ...(suite.specs ?? []).flatMap((spec) =>
      spec.tests.map((t) => ({ title: spec.title, ...t })),
    ),
    ...collect(suite.suites),
  ]);
async function run(args, directory, env = {}) {
  try {
    const result = await exec(process.execPath, [cli, "test", ...args], {
      cwd: root,
      timeout: 60_000,
      maxBuffer: 16 * 1024 * 1024,
      env: {
        ...process.env,
        CI: "1",
        NO_COLOR: "1",
        FORCE_COLOR: "0",
        E2E_VALUE_FREE: "1",
        E2E_CENSUS_RUN_ID: "journeys-adoption-check",
        E2E_CENSUS_RESULTS: join(directory, "census"),
        PLAYWRIGHT_JSON_OUTPUT_FILE: join(directory, "report.json"),
        ...env,
      },
    });
    return { code: 0, ...result };
  } catch (error) {
    return {
      code: error.code,
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? "",
    };
  }
}

test("journeys refuses before dependency collection when publication is unaccepted", async () => {
  const dir = await mkdtemp(join(tmpdir(), "journeys-refusal-"));
  const result = await run(
    ["--config=playwright.journeys.config.ts", "--list", "--reporter=list"],
    dir,
  );
  assert.equal(result.code, 1);
  assert.match(result.stdout + result.stderr, /HARNESS-GAP HR-JRN-01/);
  assert.doesNotMatch(
    result.stdout + result.stderr,
    /Cannot find module|W00_RESULT_RUN_REQUIRED/,
  );
});

test("accepted census graph retains one W00 dependency per browser", async () => {
  const dir = await mkdtemp(join(tmpdir(), "journeys-graph-"));
  const result = await run(
    [
      "--config=playwright.census.config.ts",
      "--list",
      "--reporter=json",
      "--project=chromium",
      "--project=firefox",
      "--grep=JRN-E2E-(000|003)",
    ],
    dir,
  );
  assert.equal(result.code, 0);
  const report = JSON.parse(await readFile(join(dir, "report.json"), "utf8"));
  const rows = collect(report.suites);
  for (const browser of ["chromium", "firefox"]) {
    const dependency = rows.filter(
      (row) => row.projectName === `w00-${browser}`,
    );
    assert.equal(dependency.length, 1);
    assert.match(dependency[0].title, /^WALK-E2E-000:/);
    assert.equal(rows.filter((row) => row.projectName === browser).length, 20);
  }
});

for (const browser of ["chromium", "firefox"]) {
  test(`direct ${browser} journey run skips before provisioning with no attachments`, async () => {
    let requests = 0;
    const server = createServer((_req, res) => {
      requests++;
      res.writeHead(503);
      res.end();
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const dir = await mkdtemp(join(tmpdir(), "journeys-no-seed-"));
    try {
      const url = `http://localhost:${server.address().port}`;
      const result = await run(
        [
          "--config=playwright.config.ts",
          "flows/journeys/",
          `--browser=${browser}`,
          "--workers=1",
          "--reporter=json",
          `--output=${join(dir, "artifacts")}`,
        ],
        dir,
        { E2E_API_URL: url, E2E_APP_URL: url },
      );
      assert.equal(result.code, 0);
      const report = JSON.parse(
        await readFile(join(dir, "report.json"), "utf8"),
      );
      const rows = collect(report.suites);
      assert.equal(rows.length, 20);
      assert.equal(requests, 0);
      for (const row of rows) {
        assert.equal(row.expectedStatus, "skipped");
        assert.ok(
          row.annotations.some(
            (a) =>
              a.type === "skip" &&
              a.description.includes("HARNESS-GAP HR-JRN-01"),
          ),
        );
        for (const result of row.results) {
          assert.equal(result.status, "skipped");
          assert.deepEqual(result.attachments, []);
        }
      }
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
}
