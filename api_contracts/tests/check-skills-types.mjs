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
const statements = parsed.statements.filter((statement) => {
  if (statement.name?.text?.startsWith("Skills")) return true;
  return (
    ts.isVariableStatement(statement) &&
    statement.declarationList.declarations.some((declaration) =>
      declaration.name.getText(parsed).startsWith("Skills"),
    )
  );
});
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
