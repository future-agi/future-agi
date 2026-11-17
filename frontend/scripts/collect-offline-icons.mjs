import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { createRequire } from "node:module";
import { collectBackendIcons } from "./collect-backend-icons.mjs";

const require = createRequire(import.meta.url);
const collections = require("@iconify/json/collections.json");
// Application event names, not Font Awesome identifiers.
const events = new Set([
  "fa:column-view",
  "fa:csp",
  "fa:error",
  "fa:field-view",
  "fa:height",
  "fa:propose",
  "fa:ready",
  "fa:row",
  "fa:value-view-mode",
]);
const iconName = (value) =>
  /^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9-]*$/.test(value) &&
  !events.has(value) &&
  collections[value.split(":")[0]];
const flagFiles = new Set([
  "src/sections/account/account-general.jsx",
  "src/sections/address/address-new-form.jsx",
  "src/sections/user/user-quick-edit-form.jsx",
]);
function possibilities(node) {
  if (ts.isStringLiteralLike(node)) return [node.text];
  if (ts.isParenthesizedExpression(node)) return possibilities(node.expression);
  if (ts.isConditionalExpression(node)) {
    const a = possibilities(node.whenTrue);
    const b = possibilities(node.whenFalse);
    return a && b ? [...a, ...b] : null;
  }
  return null;
}

/** The same collector powers inventory, dev transforms and production ownership. */
export function collectSourceIcons(
  root,
  file,
  text = fs.readFileSync(file, "utf8"),
) {
  const names = new Set(),
    sources = [],
    templates = [],
    unresolved = [];
  const relative = path.relative(root, file).split(path.sep).join("/");
  const lines = text.split("\n");
  const source = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.JS,
  );
  const visit = (node) => {
    const locationOf = () => ({
      file: relative,
      line:
        source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1,
    });
    const record = (value) => {
      if (!iconName(value)) return;
      names.add(value);
      sources.push({ icon: value, ...locationOf() });
    };
    if (ts.isStringLiteralLike(node)) {
      record(node.text);
      if (/^[a-z0-9]+-[a-z0-9-]+$/.test(node.text)) {
        const line =
          lines[
            source.getLineAndCharacterOfPosition(node.getStart(source)).line
          ];
        if (/icon/i.test(line))
          record(node.text.replace(/^([a-z0-9]+)-/, "$1:"));
      }
    }
    if (
      ts.isTemplateExpression(node) &&
      collections[node.head.text.split(":")[0]]
    ) {
      // Bundled SVG markup, not an Iconify name. Independently hash-tested.
      if (
        file.endsWith("/RunSources/brandLogos.js") &&
        node.getText(source) === "`mdi:${n}`"
      )
        return;
      const location = locationOf();
      let values = [node.head.text];
      for (const span of node.templateSpans) {
        const options = possibilities(span.expression);
        if (!options) {
          values = null;
          break;
        }
        values = values.flatMap((prefix) =>
          options.map((v) => prefix + v + span.literal.text),
        );
      }
      if (
        flagFiles.has(relative) &&
        node.getText(source) === "`circle-flags:${code.toLowerCase()}`"
      ) {
        values = [
          ...fs
            .readFileSync(
              path.join(root, "src/assets/data/countries.js"),
              "utf8",
            )
            .matchAll(/code:\s*"([A-Z]{2})"/g),
        ].map((m) => `circle-flags:${m[1].toLowerCase()}`);
      }
      const entry = {
        ...location,
        expression: node.getText(source),
        names: values,
      };
      templates.push(entry);
      if (values) values.forEach(record);
      else unresolved.push(entry);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return { names: [...names].sort(), sources, templates, unresolved };
}

/** Includes stories/prototype sources even when absent from a production graph. */
export function collectFrontendIcons(root) {
  const names = new Set(),
    sources = [],
    templates = [],
    unresolved = [];
  function walk(dir) {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      if (
        ["__tests__", "__snapshots__", "generated", "tests"].includes(ent.name)
      )
        continue;
      const file = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        walk(file);
        continue;
      }
      if (!/\.[jt]sx?$/.test(file) || /\.(test|spec)\./.test(file)) continue;
      const found = collectSourceIcons(root, file);
      found.names.forEach((name) => names.add(name));
      sources.push(...found.sources);
      templates.push(...found.templates);
      unresolved.push(...found.unresolved);
    }
  }
  walk(path.join(root, "src"));
  return { names: [...names].sort(), sources, templates, unresolved };
}

// Generation/contract tests require backend sources; normal frontend-only
// Docker builds use the committed consumer map, not an absent Python tree.
export function collectOfflineIcons(root) {
  const frontend = collectFrontendIcons(root);
  const backend = collectBackendIcons(root);
  return {
    ...frontend,
    names: [...new Set([...frontend.names, ...backend.names])].sort(),
    sources: [...frontend.sources, ...backend.sources],
    backend,
  };
}
