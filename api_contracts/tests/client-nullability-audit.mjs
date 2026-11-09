// Compare non-Skills generated Zod declarations against a reviewed Git baseline.
// Erasing only null wrappers proves that no other accepted value shapes changed.
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../", import.meta.url));
const require = createRequire(path.join(root, "frontend/package.json"));
const ts = require("typescript");
const relative = "frontend/src/generated/api-contracts/api.zod.ts";
// The target branch may contain newer, unrelated schema changes. Audit only
// this PR's delta; an explicit revision/file still pins a reviewed baseline.
const baseline = process.argv[2] || execFileSync(
  "git", ["merge-base", "origin/dev", "HEAD"], { cwd: root, encoding: "utf8" },
).trim();
const before = fs.existsSync(baseline)
  ? fs.readFileSync(baseline, "utf8")
  : execFileSync("git", ["show", `${baseline}:${relative}`], {
      cwd: root,
      encoding: "utf8",
      maxBuffer: 32 * 1024 * 1024,
    });
const after = fs.readFileSync(path.join(root, relative), "utf8");
const printer = ts.createPrinter({ removeComments: true });
function tokens(text) {
  const scanner = ts.createScanner(
    ts.ScriptTarget.Latest,
    true,
    ts.LanguageVariant.Standard,
    text,
  );
  const parts = [];
  while (scanner.scan() !== ts.SyntaxKind.EndOfFileToken)
    parts.push(scanner.getTokenText());
  return parts
    .filter(
      (part, index) =>
        !(part === "," && ["]", "}", ")"].includes(parts[index + 1])),
    )
    .join(" ");
}

function declarations(source) {
  const file = ts.createSourceFile(
    "contracts.ts",
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const constants = new Map();
  for (const statement of file.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      const name = declaration.name.getText(file);
      // Orval renames bound/default constants when a field becomes a union.
      if (/^[a-z].*(Min|Max|RegExp|Default)$/.test(name))
        constants.set(name, declaration.initializer);
    }
  }
  const transformed = ts.transform(file, [
    (context) => {
      const visit = (node) => {
        if (
          ts.isIdentifier(node) &&
          constants.has(node.text) &&
          !ts.isVariableDeclaration(node.parent)
        ) {
          return visit(constants.get(node.text));
        }
        node = ts.visitEachChild(node, visit, context);
        if (
          ts.isCallExpression(node) &&
          ts.isPropertyAccessExpression(node.expression)
        ) {
          const method = node.expression.name.text;
          const receiver = node.expression.expression;
          if (
            method === "default" &&
            ts.isCallExpression(receiver) &&
            ts.isPropertyAccessExpression(receiver.expression) &&
            receiver.expression.name.text === "optional"
          ) {
            return ts.factory.updateCallExpression(
              node,
              ts.factory.updatePropertyAccessExpression(
                node.expression,
                receiver.expression.expression,
                "default",
              ),
              node.typeArguments,
              node.arguments,
            );
          }
          if (method === "nullable" || method === "describe")
            return node.expression.expression;
          if (method === "nullish")
            return ts.factory.updateCallExpression(
              node,
              ts.factory.updatePropertyAccessExpression(
                node.expression,
                node.expression.expression,
                "optional",
              ),
              node.typeArguments,
              node.arguments,
            );
          if (
            method === "union" &&
            node.expression.expression.getText(file) === "zod" &&
            ts.isArrayLiteralExpression(node.arguments[0])
          ) {
            const variants = node.arguments[0].elements.filter(
              (item) =>
                !(
                  ts.isCallExpression(item) &&
                  ts.isPropertyAccessExpression(item.expression) &&
                  item.expression.name.text === "null"
                ),
            );
            if (variants.length === 1) return variants[0];
            return ts.factory.updateCallExpression(
              node,
              node.expression,
              node.typeArguments,
              [ts.factory.createArrayLiteralExpression(variants)],
            );
          }
        }
        return node;
      };
      return (node) => ts.visitNode(node, visit);
    },
  ]);
  const result = new Map();
  for (const statement of transformed.transformed[0].statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      const name = declaration.name.getText(file);
      if (/^[a-z]/.test(name) || name.toLowerCase().includes("modelhubskills"))
        continue;
      result.set(
        name,
        tokens(printer.printNode(ts.EmitHint.Unspecified, declaration, file)),
      );
    }
  }
  transformed.dispose();
  return result;
}

const oldDeclarations = declarations(before);
const newDeclarations = declarations(after);
const changed = [...oldDeclarations]
  .filter(([name, value]) => value !== newDeclarations.get(name))
  .map(([name]) => name);
if (process.argv[3])
  fs.writeFileSync(
    process.argv[3],
    JSON.stringify(
      changed.map((name) => ({
        name,
        before: oldDeclarations.get(name),
        after: newDeclarations.get(name),
      })),
      null,
      2,
    ),
  );
assert.deepEqual(changed, [], "Non-null validation changed outside Skills");
console.log(
  `PASS: ${oldDeclarations.size} existing Zod declarations unchanged except nullability.`,
);
