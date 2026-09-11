#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const repoRoot = process.cwd();

const args = process.argv.slice(2);
const files =
  args.length > 0
    ? args
    : execFileSync("git", ["diff", "--cached", "--name-only", "-z"], {
        cwd: repoRoot,
        encoding: "utf8",
      })
        .split("\0")
        .filter(Boolean);

const CODE_FILE_RE = /\.(cjs|cts|js|jsx|mjs|mts|py|ts|tsx)$/i;
const ENV_EXAMPLE_RE = /(^|\/)\.env(\..*)?\.example$/;
const blockedPathRules = [
  {
    name: "macOS metadata",
    re: /(^|\/)\.DS_Store$/,
    fix: "Remove the file from git and keep it ignored.",
  },
  {
    name: "local env file",
    re: /(^|\/)\.env($|\.)/,
    allow: ENV_EXAMPLE_RE,
    fix: "Commit .env.example instead of real local environment files.",
  },
  {
    name: "local MCP config",
    re: /(^|\/)\.mcp\.json$/,
    fix: "Keep local MCP config untracked.",
  },
  {
    name: "personal Claude permissions",
    re: /^\.claude\/settings\.json$/,
    fix: "Use .claude/settings.local.json for personal permissions.",
  },
];

const textRules = [
  {
    name: "merge conflict marker",
    re: /^(<<<<<<<|=======|>>>>>>>)(?:\s|$)/m,
    appliesTo: () => true,
    fix: "Resolve the conflict before committing.",
  },
  {
    name: "focused test",
    re: /(?:^|[^\w.])(?:describe|it|test|suite|context)\.only\s*\(|(?:^|[^\w])f(?:describe|it|test)\s*\(|pytest\.mark\.(?:only|focus)\b/,
    appliesTo: (file) => CODE_FILE_RE.test(file),
    fix: "Remove focused test markers so CI runs the full suite.",
  },
  {
    name: "debug statement",
    re: /\bdebugger\s*;|\bbreakpoint\(\s*\)|\b(?:pdb|ipdb)\.set_trace\(\s*\)/,
    appliesTo: (file) => CODE_FILE_RE.test(file),
    fix: "Remove local debug statements before committing.",
  },
];

const failures = [];

for (const rawFile of files) {
  const absoluteFile = path.isAbsolute(rawFile)
    ? rawFile
    : path.resolve(repoRoot, rawFile);
  const relativeFile = path.relative(repoRoot, absoluteFile);
  const file = relativeFile.replaceAll("\\", "/");

  for (const rule of blockedPathRules) {
    if (rule.re.test(file) && !(rule.allow && rule.allow.test(file))) {
      failures.push(`${file}: blocked ${rule.name}. ${rule.fix}`);
    }
  }

  if (!fs.existsSync(absoluteFile) || fs.statSync(absoluteFile).isDirectory()) {
    continue;
  }

  const buffer = fs.readFileSync(absoluteFile);
  if (buffer.includes(0)) {
    continue;
  }

  const text = buffer.toString("utf8");
  for (const rule of textRules) {
    if (rule.appliesTo(file) && rule.re.test(text)) {
      failures.push(`${file}: found ${rule.name}. ${rule.fix}`);
    }
  }
}

if (failures.length > 0) {
  console.error("\nStaged safety checks failed:\n");
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  console.error("");
  process.exit(1);
}
