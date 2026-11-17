/** Step 5, dependencies (SPEC §4.2 row 5): D1 only @omega/*, D2 no install scripts, D3 no other registry. */
import { block } from "./findings.mjs";

const FIELDS = ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"];
const SCRIPTS = ["preinstall", "install", "postinstall", "prepare"];
const LOCKFILES = ["package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock"];
const URL = /https?:\/\/[^\s"'`,)]+/g;

export function dependencyFindings(files) {
  const out = [];
  for (const [rel, data] of files) {
    const base = rel.slice(rel.lastIndexOf("/") + 1);
    if (base === "package.json") {
      let pkg;
      try {
        pkg = JSON.parse(data.toString("utf8"));
      } catch {
        out.push(block("dependencies", "D1", rel, "package.json does not parse", { fix: "fix the JSON" }));
        continue;
      }
      for (const field of FIELDS) {
        for (const name of Object.keys(pkg?.[field] ?? {})) {
          if (!name.startsWith("@omega/")) out.push(block("dependencies", "D1", rel, `${field} names ${name}; entries depend only on @omega/* packages`, { fix: `remove ${name}` }));
        }
      }
      for (const script of SCRIPTS) {
        if (pkg?.scripts?.[script] !== undefined) out.push(block("dependencies", "D2", rel, `a ${script} script runs at install time`, { fix: `remove scripts.${script}` }));
      }
    }
    if (base === ".npmrc" || base === ".yarnrc" || base === ".yarnrc.yml") out.push(block("dependencies", "D3", rel, `${base} can point installs at another registry`, { fix: "delete it" }));
    if (LOCKFILES.includes(base)) {
      for (const [url] of data.toString("utf8").matchAll(URL)) {
        if (!/^https:\/\/registry\.(npmjs\.org|yarnpkg\.com)\//.test(url)) {
          out.push(block("dependencies", "D3", rel, `the lockfile names ${new globalThis.URL(url).host}, not npmjs`, { fix: "delete the lockfile" }));
          break;
        }
      }
    }
  }
  return out;
}
