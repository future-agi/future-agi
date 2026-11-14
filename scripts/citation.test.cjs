// Install release-please@17.1.3 outside the checkout, then run:
// NODE_PATH=<install-dir>/node_modules node --test scripts/citation.test.cjs
// Validate the CFF schema separately with cffconvert==2.0.0 --validate.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { test } = require("node:test");
const yaml = require("js-yaml");
// Initialise the public entrypoint before loading its strategy implementation.
require("release-please");
const { Simple } = require("release-please/build/src/strategies/simple");
const { Version } = require("release-please/build/src/version");

const root = resolve(__dirname, "..");
const read = (path) => readFileSync(resolve(root, path), "utf8");
const source = read("CITATION.cff");
// JSON_SCHEMA keeps dates as strings, matching CFF's date representation.
const parse = (text) => yaml.load(text, { schema: yaml.JSON_SCHEMA });
const citation = parse(source);
const config = JSON.parse(read("release-please-config.json"));

test("citation credits the organisation and names the repository", () => {
  assert.equal(citation.title, "Future AGI");
  assert.equal(citation.authors.length, 1);
  assert.deepEqual(citation.authors[0], {
    name: "Future AGI",
    website: "https://futureagi.com",
  });
  assert.equal(
    citation["repository-code"],
    "https://github.com/future-agi/future-agi",
  );
  assert.equal(Object.hasOwn(citation, "license"), false);
  assert.match(source, /# See LICENSE and LICENSE-EE[^\n]*\nrepository-code:/);
  assert.deepEqual(citation.keywords, [
    "AI agents",
    "self-improving agents",
    "agent evaluation",
    "agent simulation",
    "LLM observability",
    "OpenTelemetry",
    "guardrails",
    "LLM gateway",
  ]);
  assert.equal(
    citation.abstract,
    "The infrastructure for self-improving AI agents. Future AGI turns an agent's traces, evaluations and simulations into improvements to the agent, and is open source and self-hostable.",
  );
});

test("citation identifies the current release and its changelog date", () => {
  const version = JSON.parse(read(".release-please-manifest.json"))["."];
  assert.equal(citation.version, version);
  const heading = read("CHANGELOG.md")
    .split("\n")
    .find((line) => line.startsWith(`## [${version}](`));
  assert.ok(heading, `missing changelog heading for ${version}`);
  const date = heading.match(/\((\d{4}-\d{2}-\d{2})\)\s*$/);
  assert.ok(date, "release heading must include a date");
  assert.equal(citation["date-released"], date[1]);
});

test("release configuration registers the citation generic updater", () => {
  assert.equal(config.packages["."]["release-type"], "simple");
  assert.deepEqual(
    config.packages["."]["extra-files"].filter(
      (file) => file.path === "CITATION.cff",
    ),
    [{ type: "generic", path: "CITATION.cff" }],
  );
});

test("configured release updater advances citation version and date together", async (t) => {
  // Use a fixed future release preparation date; never contact a remote repository.
  t.mock.timers.enable({
    apis: ["Date"],
    now: new Date("2030-02-03T12:00:00Z").getTime(),
  });
  const strategy = new Simple({
    github: {},
    targetBranch: "main",
    path: ".",
    extraFiles: config.packages["."]["extra-files"],
  });
  const updates = await strategy.extraFileUpdates(Version.parse("9.8.7"));
  const update = updates.find((entry) => entry.path === "CITATION.cff");
  assert.ok(
    update,
    "citation must participate in actual release-please updates",
  );
  assert.equal(update.createIfMissing, false);
  const updated = parse(update.updater.updateContent(source));
  assert.equal(updated.version, "9.8.7");
  assert.equal(updated["date-released"], "2030-02-03");
  assert.deepEqual(updated, {
    ...citation,
    version: "9.8.7",
    "date-released": "2030-02-03",
  });
});
