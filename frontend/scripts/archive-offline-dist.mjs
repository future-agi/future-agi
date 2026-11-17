import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

// CLOUD ONLY, immediately after the exact-head build, before lease release.
// Pull this directory with the job's raw build log and receipt. A manifest alone
// is not rescan evidence: every unmodified dist file, including fonts, icons,
// editor assets and notices, is copied alongside it.
const [distArg, outArg, expectedSha] = process.argv.slice(2);
if (!distArg || !outArg || !/^[a-f0-9]{40}$/.test(expectedSha || "")) {
  throw new Error(
    "Usage: archive-offline-dist.mjs <dist> <new-evidence-dir> <exact-sha>",
  );
}
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const git = (args) =>
  execFileSync("git", ["-C", root, ...args], { encoding: "utf8" }).trim();
const sha = git(["rev-parse", "HEAD"]);
if (sha !== expectedSha || git(["status", "--porcelain"]))
  throw new Error("Archive requires the clean exact build head");
const dist = path.resolve(distArg);
const out = path.resolve(outArg);
if (out === dist || out.startsWith(`${dist}${path.sep}`) || fs.existsSync(out))
  throw new Error("Evidence must be a new directory outside dist");
if (!fs.existsSync(path.join(dist, "index.html")))
  throw new Error("Production index.html missing");
fs.mkdirSync(out, { recursive: true, mode: 0o700 });
const files = [];
function copy(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) throw new Error("Symlink in built dist");
    if (entry.isDirectory()) {
      copy(file);
      continue;
    }
    if (!entry.isFile()) throw new Error(`Non-regular dist entry: ${file}`);
    const relative = path.relative(dist, file);
    const bytes = fs.readFileSync(file);
    const target = path.join(out, "dist", relative);
    fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
    fs.writeFileSync(target, bytes, { mode: 0o600 });
    files.push({
      file: relative.split(path.sep).join("/"),
      bytes: bytes.length,
      digest: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
    });
  }
}
copy(dist);
fs.writeFileSync(
  path.join(out, "manifest.json"),
  `${JSON.stringify(
    {
      sourceRevision: `git:${sha}`,
      purpose:
        "Raw exact-head emitted bytes; retain the build job receipt to establish source-to-build provenance.",
      files: files.sort((a, b) => a.file.localeCompare(b.file)),
    },
    null,
    2,
  )}\n`,
  { mode: 0o600 },
);
process.stdout.write(
  `Archived ${files.length} raw emitted files with SHA-256 digests.\n`,
);
