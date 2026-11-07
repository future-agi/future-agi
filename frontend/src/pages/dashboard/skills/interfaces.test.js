/* eslint-env node */
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const roots = ['src/sections/skills', 'src/api/skills', 'src/pages/dashboard/skills'];
const belongsToFeature = (file) => roots.some((root) => file === root || file.startsWith(`${root}/`));
const isProduction = (file) => !/\.(test|browser)|smoke/.test(file);
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
  const file = path.join(dir, entry.name);
  return entry.isDirectory() ? walk(file) : /\.(js|jsx|mjs)$/.test(file) ? [file] : [];
});
const parse = (file) => ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.JSX);

function auditInterfaces() {
  const files = roots.flatMap(walk);
  const exports = new Map(files.map((file) => {
    const names = new Set();
    for (const node of parse(file).statements) {
      if (ts.isExportAssignment(node)) names.add('default');
      if (ts.isExportDeclaration(node) && node.exportClause && ts.isNamedExports(node.exportClause)) {
        node.exportClause.elements.forEach((item) => names.add(item.name.text));
      }
      if (!node.modifiers?.some((item) => item.kind === ts.SyntaxKind.ExportKeyword)) continue;
      if (node.modifiers.some((item) => item.kind === ts.SyntaxKind.DefaultKeyword)) names.add('default');
      else if (node.name) names.add(node.name.text);
      else if (ts.isVariableStatement(node)) node.declarationList.declarations.forEach((item) => names.add(item.name.text));
    }
    return [file, names];
  }));
  const issues = [];
  const imported = new Set();
  for (const file of [...files, 'src/layouts/dashboard/config-navigation.jsx', 'src/routes/sections/dashboard.jsx']) {
    const visit = (node) => {
      const specifier = ts.isImportDeclaration(node) ? node.moduleSpecifier.text
        : ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword ? node.arguments[0]?.text : null;
      if (typeof specifier === 'string') {
        const base = specifier.startsWith('.') ? path.normalize(path.join(path.dirname(file), specifier)) : specifier;
        if (belongsToFeature(base)) {
          const target = [base, `${base}.js`, `${base}.jsx`, `${base}/index.js`, `${base}/index.jsx`].find((candidate) => exports.has(candidate));
          if (!target) issues.push(`${file}: missing module ${specifier}`);
          else {
            if (isProduction(file)) imported.add(target);
            const clause = node.importClause;
            const names = [
              ...(clause?.name ? ['default'] : []),
              ...(clause?.namedBindings && ts.isNamedImports(clause.namedBindings) ? clause.namedBindings.elements.map((item) => (item.propertyName || item.name).text) : []),
            ];
            for (const name of names) if (!exports.get(target).has(name)) issues.push(`${file}: missing export ${name} in ${target}`);
          }
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(parse(file));
  }
  return { issues, orphans: files.filter((file) => isProduction(file) && !imported.has(file)) };
}

describe('Skills integration boundaries', () => {
  const audit = auditInterfaces();
  it('resolves every cross-feature import and its named or default export', () => {
    expect(audit.issues).toEqual([]);
  });
  it('has no orphan production modules left by parallel implementations', () => {
    expect(audit.orphans).toEqual([]);
  });
});
