/* eslint-env node */
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';
import { matchRoutes } from 'react-router-dom';
import { paths } from 'src/routes/paths';

const roots = ['src/sections/skills', 'src/api/skills', 'src/pages/dashboard/skills'];
const belongsToFeature = (file) => roots.some((root) => file === root || file.startsWith(`${root}/`));
const isProduction = (file) => !/\.(test|browser)|smoke|(^|\/)__tests__(\/|$)/.test(file);
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
      const specifier = ts.isImportDeclaration(node) || ts.isExportDeclaration(node) ? node.moduleSpecifier?.text
        : ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword ? node.arguments[0]?.text : null;
      if (typeof specifier === 'string') {
        const base = specifier.startsWith('.') ? path.normalize(path.join(path.dirname(file), specifier)) : specifier;
        if (belongsToFeature(base)) {
          const target = [base, `${base}.js`, `${base}.jsx`, `${base}/index.js`, `${base}/index.jsx`].find((candidate) => exports.has(candidate));
          if (!target && base.endsWith('.json') && fs.existsSync(base)) {
            // JSON modules have a real default export but are data, not orphan
            // JavaScript production modules. Validate rather than skip them.
            try { JSON.parse(fs.readFileSync(base, 'utf8')); }
            catch { issues.push(`${file}: invalid JSON module ${specifier}`); }
          } else if (!target) issues.push(`${file}: missing module ${specifier}`);
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
  it('distinguishes nested test support from shipped modules without hiding production helpers', () => {
    expect(isProduction('src/sections/skills/__tests__/r3_l09/fixtures.js')).toBe(false);
    expect(isProduction('src/api/skills/context-authoring.js')).toBe(true);
    expect(isProduction('src/sections/skills/review/ReleaseInbox.jsx')).toBe(true);
  });
  it('registers literal context/review routes before leaf identities without rollout ambiguity', () => {
    let children;
    const visit = (node) => {
      if (ts.isObjectLiteralExpression(node) && node.properties.some((property) => property.name?.getText() === 'path' && property.initializer?.text === 'skills')) {
        children = node.properties.find((property) => property.name?.getText() === 'children')?.initializer;
      }
      ts.forEachChild(node, visit);
    };
    visit(parse('src/routes/sections/dashboard.jsx'));
    expect(ts.isArrayLiteralExpression(children)).toBe(true);
    const routePaths = children.elements.flatMap((node) => node.properties.filter((property) => property.name?.getText() === 'path').map((property) => property.initializer.text));
    expect(routePaths.indexOf('contexts/rollouts')).toBeLessThan(routePaths.indexOf('contexts/:contextId'));
    expect(routePaths.indexOf('reviews')).toBeLessThan(routePaths.indexOf(':skillId'));
    const routes = [{ path: '/dashboard/skills', children: routePaths.map((routePath) => ({ path: routePath })) }];
    for (const [url, expected] of [[paths.dashboard.skills.contexts, 'contexts'], [paths.dashboard.skills.contextReview('context-id'), 'contexts/:contextId/review'], [paths.dashboard.skills.contextRollout('campaign-id'), 'contexts/rollouts/:campaignId'], [paths.dashboard.skills.reviews, 'reviews'], [paths.dashboard.skills.archived, 'archived'], [paths.dashboard.skills.edit('skill-id'), ':skillId/edit']]) {
      expect(matchRoutes(routes, url).at(-1).route.path).toBe(expected);
    }
  });
  const audit = auditInterfaces();
  it('resolves every cross-feature import and its named or default export', () => {
    expect(audit.issues).toEqual([]);
  });
  it('has no orphan production modules left by parallel implementations', () => {
    expect(audit.orphans).toEqual([]);
  });
});
