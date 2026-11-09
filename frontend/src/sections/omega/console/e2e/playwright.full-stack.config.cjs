/* eslint-env node */
// Explicit real-EE lifecycle gate. Missing EE builds fail the spec's beforeAll;
// do not turn this into an optional/skip-on-missing dependency.
const base = require("./playwright.config.cjs");

module.exports = {
  ...base,
  projects: [
    {
      name: "chromium-full-stack",
      use: { browserName: "chromium" },
      testMatch: /document-lifecycle\.e2e\.cjs$/,
      grep: /Console document lifecycle with real EE Console/,
    },
  ],
};
