import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  readCode,
  withoutComments,
  withoutRegexLiterals,
} from "../../sections/omega/__tests__/copy-sweep";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

// Temporary ownership allow-list, not approved customer copy. Remove each
// exemption when its owning lane has applied the same hide/withheld ruling.
const NOT_OWNED = [
  "sections/settings/integrations/", // gateway-connectors-2197: AddIntegrationWizard
  "sections/gateway/connectors/", // gateway-connectors-2197: catalog expansion
  "routes/sections/dashboard.jsx", // broker2 wave-2 B6: dashboard routes
];
const TEST_DIRS = new Set(["__tests__", "__fixtures__", "e2e", "node_modules"]);
const TEXT_SOURCE = /\.(?:[cm]?[jt]sx?|json|jsonl|css|html|svg|md|txt)$/;
const TEST_FILE = /\.(?:test|spec|e2e)\./;
const COMING_SOON = /\bcoming\s+soon\b/i;

function sources(dir = ROOT) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return TEST_DIRS.has(entry.name) ? [] : sources(file);
    }
    return TEXT_SOURCE.test(entry.name) && !TEST_FILE.test(entry.name)
      ? [file]
      : [];
  });
}

const isOwned = (file) => {
  const relative = path.relative(ROOT, file).split(path.sep).join("/");
  return !NOT_OWNED.some((excluded) =>
    excluded.endsWith("/")
      ? relative.startsWith(excluded)
      : relative === excluded,
  );
};

// Like the Omega copy sweep, inspect source with comments and filtering regexes
// removed. Identifiers such as comingSoon are not customer-visible copy.
describe("No Coming soon copy anywhere in the frontend", () => {
  const files = sources().filter(isOwned);

  it("covers the frontend, not only the Omega tabs", () => {
    expect(files.length).toBeGreaterThan(1000);
    for (const file of [
      "components/LandingPageCard/LandingPageCard.jsx",
      "sections/common/RunSources/WhereItRuns.jsx",
      "sections/simulate/environments/BuildEnvironmentTab.jsx",
    ]) {
      expect(files).toContain(path.join(ROOT, file));
    }
  });

  it("finds no customer-visible Coming soon copy in owned sources", () => {
    const hits = files.flatMap((file) => {
      const code = readCode(file);
      return COMING_SOON.test(code) ? [path.relative(ROOT, file)] : [];
    });
    expect(hits).toEqual([]);
  });

  it("catches text, attributes, tooltips and copy constants in every case", () => {
    for (const source of [
      "<Label>Coming soon</Label>",
      '<Chip aria-label="Coming Soon" />',
      '<Tooltip title="Choosing a winner is coming soon" />',
      'const withheld = "Future AGI sandbox: COMING SOON";',
      "<span>Coming\n soon</span>",
    ]) {
      expect(
        COMING_SOON.test(withoutRegexLiterals(withoutComments(source))),
      ).toBe(true);
    }
    for (const source of [
      "// Coming soon is forbidden copy",
      "/* Coming soon is forbidden copy */",
      "const patterns = [/coming soon/i];",
      "const comingSoon = true;",
      'const label = "Not available in this deployment";',
    ]) {
      expect(
        COMING_SOON.test(withoutRegexLiterals(withoutComments(source))),
      ).toBe(false);
    }
  });
});
