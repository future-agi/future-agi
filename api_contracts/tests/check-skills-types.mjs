// Type-check the actual emitted Skills declarations and positive/negative fixtures.
// The full legacy schema has independent duplicate-name/index-signature debt;
// keep those diagnostics separate rather than disabling checks on these types.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const require = createRequire(path.join(root, "frontend/package.json"));
const ts = require("typescript");
const schemas = path.join(
  root,
  "frontend/src/generated/api-contracts/api.schemas.ts",
);
const fixture = path.join(
  root,
  "api_contracts/tests/skills-generated-types.ts",
);
const options = {
  strict: true,
  noEmit: true,
  skipLibCheck: true,
  target: ts.ScriptTarget.ES2022,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  module: ts.ModuleKind.ESNext,
};
const host = ts.createCompilerHost(options);
const getSourceFile = host.getSourceFile.bind(host);
const parsed = ts.createSourceFile(
  schemas,
  fs.readFileSync(schemas, "utf8"),
  options.target,
  true,
);
// Include types referenced by Skills declarations, including non-Skills names
// such as LatestLoadResultApi. Do not hide missing dependency diagnostics.
const declarations = new Map();
function register(name, statement) {
  const matches = declarations.get(name) || [];
  matches.push(statement);
  declarations.set(name, matches);
}
for (const statement of parsed.statements) {
  if (statement.name?.text) register(statement.name.text, statement);
  if (ts.isVariableStatement(statement)) {
    for (const d of statement.declarationList.declarations)
      register(d.name.getText(parsed), statement);
  }
}
const selected = new Set();
function include(statement) {
  if (!statement || selected.has(statement)) return;
  selected.add(statement);
  function visit(node) {
    if (ts.isTypeReferenceNode(node) || ts.isTypeQueryNode(node)) {
      const name = (node.typeName ?? node.exprName).getText(parsed);
      for (const match of declarations.get(name) || []) include(match);
    }
    ts.forEachChild(node, visit);
  }
  visit(statement);
}
for (const [name, matches] of declarations)
  if (name.startsWith("Skills")) matches.forEach(include);
const statements = parsed.statements.filter((s) => selected.has(s));
if (statements.length < 50)
  throw new Error("Skills declarations missing from generated contract");
const source = statements
  .map((statement) => statement.getFullText(parsed))
  .join("\n");
host.getSourceFile = (name, ...args) =>
  path.resolve(name) === schemas
    ? ts.createSourceFile(name, source, options.target, true)
    : getSourceFile(name, ...args);
const program = ts.createProgram([fixture], options, host);
const diagnostics = ts.getPreEmitDiagnostics(program);
if (diagnostics.length) {
  console.error(
    ts.formatDiagnosticsWithColorAndContext(diagnostics, {
      getCurrentDirectory: () => root,
      getCanonicalFileName: (name) => name,
      getNewLine: () => "\n",
    }),
  );
  process.exitCode = 1;
} else {
  console.log(
    `PASS: ${statements.length} emitted Skills declarations and positive/negative type assertions.`,
  );
}
