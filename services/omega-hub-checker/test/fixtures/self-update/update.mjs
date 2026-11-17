import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Rewrites its own module: the self-updater shape (Hermes rule 3, content rule C3).
export function update(code) {
  writeFileSync(fileURLToPath(import.meta.url), code);
  writeFileSync("main.mjs", code);
}
