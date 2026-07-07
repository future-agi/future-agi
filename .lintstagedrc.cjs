// lint-staged config — cross-platform (Windows + macOS + Linux).
//
// Problem: The previous version built paths to
//   frontend/node_modules/.bin/eslint  (and prettier)
// Those .bin entries are POSIX symlinks. Since the Node CVE-2024-27980 hardening,
// Node refuses to spawn .cmd/.bat files without shell:true, and the bare shims are
// not executable on Windows — so every `git commit` on Windows fails with
// "Task failed to spawn: node scripts/lint-staged-frontend.mjs".
//
// Fix: resolve each tool's real entry-point script from its own package.json
// and invoke it with process.execPath (the current Node binary).
// This is fully cross-platform, needs no shell, and preserves exit-code propagation
// so lint errors still block the commit.
//
// Fixes: https://github.com/future-agi/future-agi/issues/1340

'use strict';

const path = require('path');
const fs   = require('fs');

const frontendDir = path.resolve(__dirname, 'frontend');
const node        = process.execPath; // e.g. C:\Program Files\nodejs\node.exe on Windows

/**
 * Resolve the real JS entry-point for a CLI tool installed under frontend/node_modules.
 * Reads the tool's own package.json `bin` field instead of relying on the .bin shim.
 *
 * @param {string} pkg   - npm package name (e.g. "eslint")
 * @param {string} [bin] - bin name if different from pkg (e.g. "prettier")
 * @returns {string} absolute path to the JS script
 */
function resolveFrontendBin(pkg, bin) {
  const pkgJsonPath = path.join(frontendDir, 'node_modules', pkg, 'package.json');
  const pkgJson     = JSON.parse(fs.readFileSync(pkgJsonPath, 'utf8'));
  const binField    = pkgJson.bin;
  const binRelative =
    typeof binField === 'string'
      ? binField
      : binField[bin || pkg];
  return path.join(frontendDir, 'node_modules', pkg, binRelative);
}

// Resolved lazily so the config file can be required even when node_modules is absent
// (e.g. during CI image build before yarn install).
let _eslint   = null;
let _prettier = null;

function eslintBin()   { return (_eslint   ??= resolveFrontendBin('eslint',   'eslint')); }
function prettierBin() { return (_prettier ??= resolveFrontendBin('prettier', 'prettier')); }

module.exports = {
  // JS / JSX / TS / TSX — lint then format
  'frontend/src/**/*.{js,jsx,ts,tsx}': (files) => {
    const args = files.map((f) => `"${f}"`).join(' ');
    return [
      `"${node}" "${eslintBin()}" --fix ${args}`,
      `"${node}" "${prettierBin()}" --write ${args}`,
    ];
  },

  // JSON / CSS / MD — format only
  'frontend/src/**/*.{json,css,md}': (files) => {
    const args = files.map((f) => `"${f}"`).join(' ');
    return `"${node}" "${prettierBin()}" --write ${args}`;
  },
};
