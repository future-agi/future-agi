// Tooltips on these pages use the app's CustomTooltip, never MUI's Tooltip directly.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const listFile = path.resolve(root, "..", "IntegrationsList.jsx");

const sourceFiles = (dir) =>
  fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory())
      return entry.name === "__tests__" ? [] : sourceFiles(full);
    return /\.(js|jsx)$/.test(entry.name) ? [full] : [];
  });

const MUI_TOOLTIP =
  /import\s*\{[^}]*\bTooltip\b[^}]*\}\s*from\s*"@mui\/material"|from\s*"@mui\/material\/Tooltip"/;

describe("integrations pages tooltips", () => {
  it("never import MUI's Tooltip", () => {
    const offenders = [...sourceFiles(root), listFile]
      .filter((file) => MUI_TOOLTIP.test(fs.readFileSync(file, "utf8")))
      .map((file) => path.relative(root, file));
    expect(offenders).toEqual([]);
  });
});
