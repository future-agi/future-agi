// options-truth L3 G10 (copy:status-words, S1): no user-visible text in the
// Datasets + Experiments surface says an option is unfinished. Owner, Oct 19:
// "nothing should be coming soon. all should be made right now."
//   - always: the scanner itself (comments are not user-visible; strings,
//     JSX text, generated scripts and example files are)
//   - FI_REPO_SCANS=1 (gates the final merge): every file of the prototype
//     surface — the dataset / experiment / compare sections and the shared
//     `common` parts they use, the mock world, the trace drawer and the r4
//     examples. Other product areas (Simulate, Annotations, Integrations,
//     Sync, Agents) are outside this prototype and keep their own copy.
// "preview" for a preview pane or a command preview is fine, so only the
// status forms of it are matched (`(Preview)`, `· Preview`, `Preview ·`,
// `Preview:` and a bare "Preview" string).
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const STATUS_WORDS = [
  /coming soon/i,
  /works today/i,
  /not in (the )?prototype/i,
  /not available yet/i,
  /off in this demo/i,
  /in the prototype/i,
  /not released/i,
  /\(prototype\)/i,
  /\(demo\)/i,
  /demo stand-in/i,
  /node ids not verified/i,
  /NOT_IN_PROTOTYPE/,
  /\(Preview\)/,
  /· Preview\b/,
  /\bPreview:/,
  /\|\|\s*["'`]Preview["'`]/,
  /until (the |our )?(SDK|API|CLI|backend|futureagi|connect|feature)[^.\n"'`]{0,30} ships/i,
  /\(planned\)|\bplanned (futureagi|SDK|API|parameter|connect)\b|^\s*["'`]*Planned:/i,
  /^\s*(#|\/\/)\s*futureagi \d+\.\d+ ·/,
  // options-truth L5 G5: no "in this prototype" hedge, and the signed-in
  // user is a person (Maya Chen), never a placeholder name.
  /\bin this prototype\b/i,
  /\bDemo Owner\b/,
];

// Regex literals after `(`, `,` or `=` are code (e.g. a `.replace()` that
// strips an old status word), never text a user reads.
const REGEX_LITERAL =
  /([(,=]\s*)\/(?![*/])(?:\\.|\[[^\]\n]*\]|[^/\n\\[])+\/[dgimsuy]*/g;
// Strings first, so `//` inside a URL string is not a comment.
const TOKENS =
  /("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\[\s\S]|[^`\\])*`)|(\/\*[\s\S]*?\*\/|\/\/[^\n]*)/g;

/** JS / JSX source with its comments blanked (line numbers kept). */
function stripComments(text) {
  return String(text ?? "")
    .replace(TOKENS, (m, str, comment) =>
      str !== undefined ? str : comment.replace(/[^\n]/g, " "),
    )
    .replace(REGEX_LITERAL, (m, lead) => `${lead}/re/`);
}

/** `[{line, text, word}]` for every status word in one file's text. */
function statusHits(text, { js = false } = {}) {
  const body = js ? stripComments(text) : String(text ?? "");
  const hits = [];
  body.split("\n").forEach((l, i) => {
    const word = STATUS_WORDS.find((p) => p.test(l));
    if (word) hits.push({ line: i + 1, text: l.trim(), word: word.source });
  });
  return hits;
}

describe("S1 · the status-word scanner", () => {
  it("finds status words in strings and JSX text, not in comments", () => {
    const src = [
      "// Coming soon: not shown to anyone",
      "/* (Preview) in a block comment */",
      'const a = "Coming soon";',
      "const b = <Chip label={`Code · Preview`} />;",
      'const url = "https://x.y//z"; // works today',
      "const c = <Box>Not in prototype</Box>;",
      'const d = "Preview pane";',
    ].join("\n");
    expect(statusHits(src, { js: true }).map((h) => h.line)).toEqual([3, 4, 6]);
  });
  it("keeps a preview pane, a reply that ships as planned and a version fact", () => {
    [
      "Show the preview",
      "A-9121 ships as planned.",
      "Connected · futureagi 0.9.1 · wss",
      "Preview · Code check",
      "Yes, until it ships. Reply with the new address.",
      'const t = s.replace(/ · Works today$/, "");',
    ].forEach((t) => expect(statusHits(t, { js: true })).toEqual([]));
  });
  it("flags a version marker used as a status and a planned API", () => {
    expect(statusHits("# futureagi 0.9 · fi.connect and fi run")).toHaveLength(
      1,
    );
    expect(statusHits("the planned futureagi 0.9 connect API")).toHaveLength(1);
    expect(statusHits("Yes, until the SDK ships.")).toHaveLength(1);
    expect(
      statusHits('label={SOURCE_LABELS[s] || "Preview"}', { js: true }),
    ).toHaveLength(1);
  });
  it("flags an 'in this prototype' hedge and the placeholder signed-in name", () => {
    expect(
      statusHits('const f = "In this prototype Falcon answers";', { js: true }),
    ).toHaveLength(1);
    expect(
      statusHits('const me = user?.name ?? "Demo Owner";', { js: true }),
    ).toHaveLength(1);
    expect(
      statusHits('const me = user?.name ?? "Maya Chen";', { js: true }),
    ).toEqual([]);
  });
});

// ------------------------------------------------- repo scan (final merge)
const FRONTEND = process.cwd();
const ROOTS = [
  "src/sections/develop-detail",
  "src/sections/experiment-detail",
  "src/sections/task-compare",
  "src/sections/develop",
  "src/sections/common",
  "src/sections/connect",
  "src/sections/workbench",
  "src/sections/workbench-v2",
  "src/_mock/prototype",
  "src/components/traceDetail",
  "../examples/experiments/r4",
].map((r) => path.resolve(FRONTEND, r));
const TEXT = /\.(jsx?|tsx?|mjs|py|md|sh|ya?ml|txt|json)$/;
const JS = /\.(jsx?|tsx?|mjs)$/;
function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  fs.readdirSync(dir, { withFileTypes: true }).forEach((d) => {
    if (
      d.name === "node_modules" ||
      d.name === "__tests__" ||
      d.name.startsWith(".")
    )
      return;
    const p = path.join(dir, d.name);
    if (d.isDirectory()) walk(p, out);
    else if (TEXT.test(d.name)) out.push(p);
  });
  return out;
}

describe.skipIf(process.env.FI_REPO_SCANS !== "1")(
  "S1 · repo scan (FI_REPO_SCANS=1)",
  () => {
    it("no user-visible status word in the prototype surface", () => {
      const hits = [];
      ROOTS.flatMap((r) => walk(r))
        // design notes for developers (PRIMITIVES*.md, README.md under src)
        .filter(
          (f) =>
            !(f.endsWith(".md") && f.includes(`${path.sep}src${path.sep}`)),
        )
        .forEach((f) => {
          statusHits(fs.readFileSync(f, "utf8"), { js: JS.test(f) }).forEach(
            (h) =>
              hits.push(
                `${path.relative(FRONTEND, f)}:${h.line}: ${h.text.slice(0, 160)}`,
              ),
          );
        });
      expect(hits, hits.join("\n")).toEqual([]);
    });
  },
);
