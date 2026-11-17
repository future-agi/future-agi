"use strict";
const { readBinding, validateArguments } = require("./consumer.cjs");
// Playwright --list does not run globalSetup. A discovery binding can therefore
// list even DARK/owed titles but can NEVER execute their callbacks accidentally.
module.exports = async function setup() {
  const binding = readBinding();
  validateArguments(binding, process.argv);
  if (binding.mode !== "execute")
    throw new Error("F01 discovery is --list only; no fixture execution");
};
