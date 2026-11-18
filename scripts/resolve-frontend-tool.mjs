import { accessSync, constants, readFileSync, statSync } from "node:fs";
import path from "node:path";

// Run the package's JavaScript entry point with Node; Windows .cmd shims
// aren't executables that spawnSync can launch directly without a shell.
export function resolveFrontendTool(frontendDir, name) {
  const dir = path.join(frontendDir, "node_modules", name);
  try {
    const { bin } = JSON.parse(
      readFileSync(path.join(dir, "package.json"), "utf8"),
    );
    const entry = typeof bin === "string" ? bin : bin?.[name];
    if (typeof entry !== "string" || !entry) return null;
    const script = path.join(dir, entry);
    if (!statSync(script).isFile()) return null;
    accessSync(script, constants.R_OK);
    return script;
  } catch {
    // An incomplete installation should produce the caller's reinstall hint.
    return null;
  }
}
