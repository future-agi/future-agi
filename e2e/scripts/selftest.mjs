import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const e2eRoot = fileURLToPath(new URL('../', import.meta.url));
export const docsSuite = 'harness/skills-r3/docs.test.mjs';

/** Keep docs as an explicit, fail-closed gate; discover every other harness suite. */
export function discoverSelftests(root = e2eRoot) {
  const visit = (directory) => readdirSync(join(root, directory), { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`;
    return entry.isDirectory() ? visit(path) : entry.isFile() && path.endsWith('.test.mjs') ? [path] : [];
  });
  return [...visit('scripts'), ...visit('harness/skills-r3')].filter((path) => path !== docsSuite).sort();
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log('Harness primitives only, not product qualification. Documentation gate: CTXHUB_DOCS_ROOT=<pinned-checkout> yarn selftest:docs');
  const result = spawnSync(process.execPath, ['--test', ...discoverSelftests()], { cwd: e2eRoot, stdio: 'inherit' });
  if (result.error) console.error(result.error.message);
  process.exitCode = result.status ?? 1;
}
