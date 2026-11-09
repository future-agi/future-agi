// options-truth L3 G4 C6 (S3 / I5): `fi` is a reserved word in bash and
// zsh (`fi connect x` → "syntax error near unexpected token `fi'"), so the
// CLI is `futureagi`. Every command a screen prints must run as typed:
// none starts with `fi `, and every shell text parses under `bash -n` and
// `zsh -n` (a shell that is not installed is skipped).
//   - always: every command and Copy all script the G4 generators print
//     (code entries × languages × forms, with picked rows and limits;
//     connect for code, harnesses and voice workers)
//   - FI_REPO_SCANS=1 (gates the final merge): every file under
//     src/sections, src/_mock/prototype and examples/experiments/r4, and
//     the shell blocks of the example READMEs and commands.txt files
import { describe, expect, it } from "vitest";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { snippetFor } from "../snippets";
import { copyAllSetup } from "../setupRecipe";
import { FRAMEWORKS } from "../catalogEntries";

const SHELLS = ["bash", "zsh"].filter(
  (sh) => spawnSync(sh, ["-c", "true"]).status === 0,
);
/** null when `text` parses in every installed shell, else the error. */
function parseError(text) {
  for (const sh of SHELLS) {
    const r = spawnSync(sh, ["-n"], { input: text, encoding: "utf8" });
    if (r.status !== 0) return `${sh}: ${r.stderr}`;
  }
  return null;
}
/** A command or comment line that starts the reserved word as a command. */
const FI_LINE = /(^|[\s`'"(>$|;&])fi (connect|run)\b/;
const startsFi = (text) =>
  String(text ?? "")
    .split("\n")
    .some((l) => /^\s*(#\s*|\/\/\s*)?fi (connect|run)\b/.test(l));

const IDS = {
  datasetId: "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06",
  experimentId: "e6000000-0000-4000-8000-000000000002",
  runId: "a6000000-0000-4000-8000-00000000004a",
  runLabel: "#4A",
  tries: 3,
  agentName: "support_agent",
};
const SANDBOX = {
  name: "acme-support-sandbox",
  envVar: "ACME_SANDBOX_URL",
  tools: ["lookup_order", "update_address", "issue_refund"],
};
const RUN_FLAGS = {
  runId: IDS.runId,
  rows: ["T5", "T12"],
  trials: 3,
  limits: { concurrency: 8, max_cost_usd: 2.5, timeout_s: 90 },
  params: { model: "claude-haiku-4-5", temperature: 0.7 },
};
// dataset integration FE-0: Your code is absent from the picker; its generators stay
// until FE-1 replaces them, so they must still print a shell that parses.
const codeEntries = FRAMEWORKS;

/** Every shell text the code drawer prints for one entry and language. */
function codeTexts(entry, lang) {
  const out = [];
  [
    { ...IDS, sandbox: SANDBOX },
    { ...IDS, sandbox: SANDBOX, run: RUN_FLAGS },
    { ...IDS, run: { ...RUN_FLAGS, noSandbox: true }, sandbox: SANDBOX },
    {},
  ].forEach((ids) => {
    const s = snippetFor({ kind: "code", entryId: entry.id, lang, ids });
    if (!s) return;
    [
      s.tryCommand,
      s.runCommand,
      s.dryRunCommand,
      s.mockCommand,
      s.noSandboxCommand,
    ]
      .filter(Boolean)
      .forEach((c) => out.push([`${entry.id} ${lang} command`, c]));
    (s.steps ?? [])
      .filter((st) => st.lang === "bash")
      .forEach((st) =>
        out.push([`${entry.id} ${lang} step ${st.id}`, st.code]),
      );
    const install = (s.steps ?? []).find((st) => st.id === "install");
    const sandbox = (s.steps ?? []).find((st) => st.id === "sandbox");
    const all = copyAllSetup({
      install,
      sandbox,
      tryCommand: s.tryCommand,
      needs: [{ file: s.fileName, n: 1 }],
    });
    out.push([`${entry.id} ${lang} Copy all`, all.copy]);
  });
  return out;
}

describe("C6 · the G4 generators print `futureagi`, and it parses", () => {
  it.each(codeEntries.map((e) => [e.id, e]))("code · %s", (_id, entry) => {
    (entry.languages ?? ["python"]).forEach((lang) => {
      codeTexts(entry, lang).forEach(([where, text]) => {
        expect(startsFi(text), `${where}: ${text}`).toBe(false);
        expect(parseError(text), `${where}:\n${text}`).toBeNull();
      });
    });
  });
});

// ------------------------------------------------- repo scan (final merge)
// vitest runs from frontend/ (as snippetsMatchExamples.test.js assumes)
const FRONTEND = process.cwd();
const ROOTS = [
  path.join(FRONTEND, "src/sections"),
  path.join(FRONTEND, "src/_mock/prototype"),
  path.resolve(FRONTEND, "../examples/experiments/r4"),
];
const TEXT = /\.(jsx?|tsx?|mjs|py|md|sh|ya?ml|txt|json)$/;
function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  fs.readdirSync(dir, { withFileTypes: true }).forEach((d) => {
    if (d.name === "node_modules" || d.name.startsWith(".")) return;
    const p = path.join(dir, d.name);
    if (d.isDirectory()) walk(p, out);
    else if (TEXT.test(d.name)) out.push(p);
  });
  return out;
}
const SELF = path.join(
  FRONTEND,
  "src/sections/common/RunSources/__tests__/cliName.test.js",
);

describe.skipIf(process.env.FI_REPO_SCANS !== "1")(
  "C6 · repo scan (FI_REPO_SCANS=1)",
  () => {
    const files = ROOTS.flatMap((r) => walk(r)).filter((f) => f !== SELF);
    it("no printed command starts with `fi `", () => {
      const hits = [];
      files.forEach((f) =>
        fs
          .readFileSync(f, "utf8")
          .split("\n")
          .forEach((l, i) => {
            if (FI_LINE.test(l))
              hits.push(`${path.relative(FRONTEND, f)}:${i + 1}: ${l.trim()}`);
          }),
      );
      expect(hits, hits.join("\n")).toEqual([]);
    });
    it("the examples' shell blocks and commands.txt parse in bash and zsh", () => {
      const bad = [];
      files
        .filter((f) => /README\.md$|commands\.txt$/.test(f))
        .forEach((f) => {
          const text = fs.readFileSync(f, "utf8");
          const blocks = f.endsWith(".txt")
            ? [text]
            : [...text.matchAll(/```(?:bash|sh|shell)\n([\s\S]*?)```/g)].map(
                (m) => m[1],
              );
          blocks.forEach((b) => {
            const err = parseError(b);
            if (err) bad.push(`${path.relative(FRONTEND, f)}: ${err}`);
          });
        });
      expect(bad, bad.join("\n")).toEqual([]);
    });
  },
);
