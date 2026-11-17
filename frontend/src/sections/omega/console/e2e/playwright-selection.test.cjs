/* eslint-env node */
// No browser/build/Playwright install is needed: register the actual lifecycle
// spec, then check its titles and setup gates against the actual configs.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const defaults = require("./playwright.config.cjs");
const SPEC = path.join(__dirname, "document-lifecycle.e2e.cjs");
const FULL_STACK = path.join(__dirname, "playwright.full-stack.config.cjs");

function lifecycleCases(hasEE) {
  const cases = [];
  let suite;
  const declare = (title) => {
    cases.push({ title: `${suite.title} ${title}`, beforeAll: suite.beforeAll });
  };
  declare.describe = (title, body) => {
    suite = { title, beforeAll: [] };
    body();
    suite = undefined;
  };
  declare.beforeAll = (hook) => suite.beforeAll.push(hook);
  declare.beforeEach = () => {};
  declare.skip = declare.describe.skip = () => {
    assert.fail("Lifecycle cases must be selected by config, never skipped");
  };
  runInNewContext(fs.readFileSync(SPEC, "utf8"), {
    require: (id) => {
      if (id === "playwright/test") {
        return {
          test: declare,
          expect: (actual, message) => ({
            toBe: (expected) => assert.equal(actual, expected, message),
          }),
        };
      }
      if (id === "./helpers.cjs") {
        return {
          EE: "http://localhost:3062",
          FAKE: "http://localhost:3061",
          HAS_EE: hasEE,
        };
      }
      throw new Error(`Unexpected lifecycle dependency: ${id}`);
    },
  });
  assert.equal(cases.length, 7);
  return cases;
}

function matches(patterns, value) {
  if (!patterns) return false;
  return (Array.isArray(patterns) ? patterns : [patterns]).some((pattern) =>
    pattern.test(value),
  );
}

function selected(config, project, entry) {
  const title = `${project.name} document-lifecycle.e2e.cjs ${entry.title}`;
  return (
    matches(project.testMatch ?? config.testMatch, SPEC) &&
    !matches(project.testIgnore ?? config.testIgnore, SPEC) &&
    matches(project.grep ?? config.grep ?? /.*/, title) &&
    !matches(project.grepInvert ?? config.grepInvert, title)
  );
}

const isRealEE = (entry) =>
  entry.title.startsWith("Console document lifecycle with real EE Console ");

// Default browsers must keep the observer control and all three stand-in
// lifecycle cases, without selecting the strict real-EE setup gate.
test("default projects retain observer and stand-in lifecycle without selecting the strict EE gate", () => {
  const cases = lifecycleCases(false);
  const standIn = cases.filter((entry) =>
    entry.title.startsWith("Console document lifecycle with protocol stand-in "),
  );
  const observer = cases.filter((entry) =>
    entry.title.startsWith("Console lifecycle observer "),
  );
  assert.equal(standIn.length, 3);
  assert.equal(observer.length, 1);
  assert.deepEqual(
    defaults.projects.map((project) => project.name),
    ["chromium", "firefox", "webkit", "chromium-notifications"],
  );
  for (const project of defaults.projects) {
    const chosen = cases.filter((entry) => selected(defaults, project, entry));
    assert.deepEqual(
      chosen.map((entry) => entry.title),
      project.name === "chromium-notifications"
        ? []
        : [...observer, ...standIn].map((entry) => entry.title),
      `${project.name} must not require the optional EE build`,
    );
    for (const entry of chosen) {
      for (const hook of entry.beforeAll) hook();
    }
  }
});

test("real-EE lifecycle still fails its setup gate when the build is absent", () => {
  const cases = lifecycleCases(false).filter(isRealEE);
  assert.equal(cases.length, 3);
  for (const entry of cases) {
    assert.ok(entry.beforeAll.length > 0);
    assert.throws(
      () => entry.beforeAll.forEach((hook) => hook()),
      /CONSOLE_E2E_EE_DIST must supply the paired probe-aware EE build/,
    );
  }
});

test("explicit full-stack project selects exactly the three strict real-EE cases", () => {
  assert.ok(fs.existsSync(FULL_STACK), "An explicit full-stack config is required");
  const config = require(FULL_STACK);
  assert.deepEqual(
    config.projects.map((project) => project.name),
    ["chromium-full-stack"],
  );
  assert.deepEqual(config.webServer, defaults.webServer);
  for (const hasEE of [false, true]) {
    const cases = lifecycleCases(hasEE);
    const chosen = cases.filter((entry) =>
      selected(config, config.projects[0], entry),
    );
    assert.equal(chosen.length, 3);
    assert.ok(chosen.every(isRealEE));
    for (const entry of chosen) {
      const setup = () => entry.beforeAll.forEach((hook) => hook());
      if (hasEE) assert.doesNotThrow(setup);
      else assert.throws(setup, /CONSOLE_E2E_EE_DIST/);
    }
  }
});
