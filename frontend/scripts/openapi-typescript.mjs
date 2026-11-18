import ts from "typescript";

function parse(source) {
  const file = ts.createSourceFile(
    "generated.ts",
    source,
    ts.ScriptTarget.Latest,
    true,
  );
  if (file.parseDiagnostics.length) {
    throw new Error("Cannot normalize invalid generated TypeScript syntax.");
  }
  return file;
}

function applyEdits(source, edits) {
  for (const { start, end, text } of edits.sort((a, b) => b.start - a.start)) {
    source = source.slice(0, start) + text + source.slice(end);
  }
  return source;
}

// Orval disambiguates functions for duplicate operationIds, but emits their
// shared query/response aliases twice. Share only identical aliases; a changed
// definition must fail generation rather than silently bind to the wrong route.
export function deduplicateTypeAliases(source) {
  const file = parse(source);
  const printer = ts.createPrinter({ removeComments: true });
  const aliases = new Map();
  const edits = [];
  for (const statement of file.statements) {
    if (!ts.isTypeAliasDeclaration(statement)) continue;
    const name = statement.name.text;
    const signature = printer.printNode(
      ts.EmitHint.Unspecified,
      statement,
      file,
    );
    if (!aliases.has(name)) {
      aliases.set(name, signature);
    } else if (aliases.get(name) !== signature) {
      throw new Error(`Conflicting generated type alias: ${name}`);
    } else {
      edits.push({
        start: statement.getFullStart(),
        end: statement.end,
        text: "",
      });
    }
  }
  return applyEdits(source, edits);
}

// Scope nullability to one object in named exports, so unrelated properties with
// the same name keep their contracts. Missing anchors fail regeneration.
export function makeObjectPropertiesNullable(
  source,
  exports,
  property,
  fields,
) {
  const file = parse(source);
  const edits = [];
  const remaining = new Set(exports);
  function objectShape(node) {
    if (
      !ts.isCallExpression(node) ||
      !ts.isPropertyAccessExpression(node.expression)
    ) {
      throw new Error("Expected a generated Zod object chain.");
    }
    if (
      node.expression.name.text === "object" &&
      ts.isObjectLiteralExpression(node.arguments[0])
    ) {
      return node.arguments[0];
    }
    return objectShape(node.expression.expression);
  }
  for (const statement of file.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (
        !ts.isIdentifier(declaration.name) ||
        !remaining.has(declaration.name.text)
      )
        continue;
      const parent = objectShape(declaration.initializer).properties.find(
        (node) => ts.isPropertyAssignment(node) && node.name.text === property,
      );
      if (!parent) throw new Error(`Missing generated property: ${property}`);
      const shape = objectShape(parent.initializer);
      for (const field of fields) {
        const node = shape.properties.find(
          (member) =>
            ts.isPropertyAssignment(member) && member.name.text === field,
        );
        if (!node)
          throw new Error(`Missing generated property: ${property}.${field}`);
        edits.push({
          start: node.initializer.end,
          end: node.initializer.end,
          text: ".nullable()",
        });
      }
      remaining.delete(declaration.name.text);
    }
  }
  if (remaining.size)
    throw new Error(`Missing generated exports: ${[...remaining].join(", ")}`);
  return applyEdits(source, edits);
}

// Composite default constants lose literal types through normal TS widening.
// Narrow their string values, NOT their containers: readonly arrays from a
// blanket `as const` are not assignable to Zod's mutable array inputs. Invalid
// defaults still fail type-check; this does not assert a schema or use `any`.
export function preserveDefaultLiterals(source) {
  const file = parse(source);
  const edits = [];
  function visit(node) {
    if (ts.isPropertyAssignment(node)) {
      visit(node.initializer);
    } else if (ts.isObjectLiteralExpression(node)) {
      node.properties.forEach(visit);
    } else if (ts.isArrayLiteralExpression(node)) {
      node.elements.forEach(visit);
    } else if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node)
    ) {
      edits.push({ start: node.end, end: node.end, text: " as const" });
    }
  }
  for (const statement of file.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (
        ts.isIdentifier(declaration.name) &&
        declaration.name.text.endsWith("Default") &&
        declaration.initializer &&
        (ts.isObjectLiteralExpression(declaration.initializer) ||
          ts.isArrayLiteralExpression(declaration.initializer))
      ) {
        visit(declaration.initializer);
      }
    }
  }
  return applyEdits(source, edits);
}
