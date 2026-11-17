import fs from "node:fs";
import path from "node:path";

// Backend-authored defaults are part of the frontend asset contract, too.
// Do not inspect deployment configuration, secrets, tests or ee/cloud.
export function collectBackendIcons(frontendRoot) {
  const root = path.resolve(frontendRoot, "../futureagi");
  const sources = [];
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (
        ["tests", "__pycache__", "node_modules", ".venv"].includes(entry.name)
      )
        continue;
      const file = path.join(dir, entry.name);
      if (path.relative(root, file) === path.join("ee", "cloud")) continue;
      if (entry.isDirectory()) {
        walk(file);
        continue;
      }
      if (!/\.(py|ya?ml|json)$/.test(file)) continue;
      const lines = fs.readFileSync(file, "utf8").split("\n");
      lines.forEach((line, index) => {
        if (!/\bicon(?:_name)?\b/i.test(line) || /^\s*#/.test(line)) return;
        for (const match of line.matchAll(
          /\b([a-z][a-z0-9-]*:[a-z0-9][a-z0-9-]*)\b/g,
        )) {
          sources.push({
            icon: match[1],
            file: path.relative(frontendRoot, file),
            line: index + 1,
          });
        }
      });
    }
  }
  // A frontend-only snapshot cannot prove the backend portion of the contract.
  if (!fs.existsSync(root))
    throw new Error(`Backend icon source missing: ${root}`);
  walk(root);
  return {
    names: [...new Set(sources.map(({ icon }) => icon))].sort(),
    sources,
  };
}
