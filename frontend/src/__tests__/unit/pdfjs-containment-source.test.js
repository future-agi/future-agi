import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { expect, it } from "vitest";

// A non-literal URL argument avoids Vite/Vitest's browser asset URL rewrite.
const relativeSrcRoot = "../../";
const srcRoot = fileURLToPath(new URL(relativeSrcRoot, import.meta.url));

function* sourceFiles(dir) {
  for (const entry of fs
    .readdirSync(dir, { withFileTypes: true })
    .sort((a, b) => a.name.localeCompare(b.name))) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!["__tests__", "__mocks__", "node_modules"].includes(entry.name))
        yield* sourceFiles(file);
    } else if (
      entry.isFile() &&
      /\.[cm]?[jt]sx?$/.test(entry.name) &&
      !/\.(test|spec|stories|d)\.[cm]?[jt]sx?$/.test(entry.name)
    ) {
      yield file;
    }
  }
}

function unwrap(node) {
  while (node && ts.isParenthesizedExpression(node)) node = node.expression;
  return node;
}

function nameOf(node) {
  if (!node) return undefined;
  if (ts.isIdentifier(node) || ts.isStringLiteral(node)) return node.text;
  if (ts.isPropertyAccessExpression(node)) return node.name.text;
  if (ts.isElementAccessExpression(node))
    return nameOf(node.argumentExpression);
  return undefined;
}

// Require an explicit false on the actual options object. A comment, a nearby
// safe call, or a later spread that could re-enable eval cannot satisfy this.
function disablesEval(node) {
  node = unwrap(node);
  if (!node || !ts.isObjectLiteralExpression(node)) return false;
  for (const prop of [...node.properties].reverse()) {
    if (ts.isSpreadAssignment(prop)) return false;
    if (nameOf(prop.name) === "isEvalSupported") {
      return (
        ts.isPropertyAssignment(prop) &&
        unwrap(prop.initializer).kind === ts.SyntaxKind.FalseKeyword
      );
    }
  }
  return false;
}

function viewerDisablesEval(node) {
  for (const attr of [...node.attributes.properties].reverse()) {
    if (ts.isJsxSpreadAttribute(attr)) return false;
    if (attr.name.text !== "transformGetDocumentParams") continue;
    const transform = unwrap(
      attr.initializer && ts.isJsxExpression(attr.initializer)
        ? attr.initializer.expression
        : null,
    );
    // Keep the launch mitigation visible at each call site, not in an opaque
    // callback or props spread. Behavioral tests separately check preservation.
    return Boolean(
      transform &&
        ts.isArrowFunction(transform) &&
        disablesEval(transform.body),
    );
  }
  return false;
}

it("lists every PDF document call/viewer and requires explicit eval containment", () => {
  const sites = [];
  for (const file of sourceFiles(srcRoot)) {
    const source = fs.readFileSync(file, "utf8");
    if (!/getDocument|Viewer|@react-pdf-viewer/.test(source)) continue;
    const ast = ts.createSourceFile(
      file,
      source,
      ts.ScriptTarget.Latest,
      true,
      /\.[cm]?tsx?$/.test(file) ? ts.ScriptKind.TSX : ts.ScriptKind.JSX,
    );
    const documentNames = new Set(["getDocument"]);
    const viewerNames = new Set(["Viewer"]);
    for (const stmt of ast.statements) {
      if (!ts.isImportDeclaration(stmt)) continue;
      const bindings = stmt.importClause?.namedBindings;
      if (!bindings || !ts.isNamedImports(bindings)) continue;
      for (const specifier of bindings.elements) {
        const original = (specifier.propertyName || specifier.name).text;
        if (original === "getDocument") documentNames.add(specifier.name.text);
        if (
          original === "Viewer" &&
          stmt.moduleSpecifier.text.startsWith("@react-pdf-viewer/")
        )
          viewerNames.add(specifier.name.text);
      }
    }
    const add = (node, kind, contained) => {
      const { line } = ast.getLineAndCharacterOfPosition(node.getStart(ast));
      sites.push({
        file: path.relative(srcRoot, file),
        line: line + 1,
        kind,
        contained,
      });
    };
    const visit = (node) => {
      if (
        ts.isCallExpression(node) &&
        documentNames.has(nameOf(node.expression))
      ) {
        add(node, "getDocument", disablesEval(node.arguments[0]));
      }
      if (
        (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
        viewerNames.has(nameOf(node.tagName))
      ) {
        add(node, "Viewer", viewerDisablesEval(node));
      }
      ts.forEachChild(node, visit);
    };
    visit(ast);
  }

  const inventory = sites
    .map(
      ({ file, line, kind, contained }) =>
        `${file}:${line} ${kind} ${contained ? "contained" : "MISSING CONTAINMENT"}`,
    )
    .join("\n");
  // These checks also prevent an accidentally empty or wrong-root scan passing.
  expect(sites.map(({ file, kind }) => `${file} ${kind}`)).toEqual(
    expect.arrayContaining([
      "components/PdfPreviewDrawer.jsx Viewer",
      "sections/common/Values/MediaViewers.jsx getDocument",
    ]),
  );
  expect(
    sites.filter(({ contained }) => !contained),
    inventory,
  ).toEqual([]);
});
