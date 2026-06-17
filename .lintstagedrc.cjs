module.exports = {
  "**/*": "node scripts/check-staged-safety.mjs",
  "futureagi/**/*.py": "scripts/lint-staged-python.sh",
  "frontend/**/*.{js,jsx,ts,tsx,mjs,mts,json,yaml,yml,css,scss,md,mdx}":
    "node scripts/lint-staged-frontend.mjs",
  "*.{js,cjs,mjs,json,yaml,yml,md,mdx}":
    "node scripts/lint-staged-root-format.mjs",
  "api_contracts/filter_contract.json": [
    "node scripts/lint-staged-root-format.mjs",
    "yarn --cwd frontend contracts:check",
  ],
  "docs/**/*.{md,mdx}": "node scripts/lint-staged-root-format.mjs",
  ".github/**/*.{json,yaml,yml,md,mdx}":
    "node scripts/lint-staged-root-format.mjs",
  "scripts/**/*.{js,cjs,mjs,json,md}":
    "node scripts/lint-staged-root-format.mjs",
};
