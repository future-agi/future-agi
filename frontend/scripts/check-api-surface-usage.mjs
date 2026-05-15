import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";
import { API_SURFACE_PATHS } from "../src/api/contracts/api-surface.generated.js";

const traverse = traverseModule.default;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const srcRoot = path.join(frontendRoot, "src");
const scriptsRoot = path.join(frontendRoot, "scripts");

const API_PATH_RE =
  /^\/(?:model-hub\/(?:annotation-tasks|annotation-queues|annotations-labels|annotations|scores|ai-filter|dataset\/.*annotation-summary)|tracer\/(?:bulk-annotation|get-annotation-labels|trace-annotation|project-version\/add_annotations|observation-span|project|trace-session|trace|dashboard|users)(?:\/|$)|api\/traces)/;

const ALLOWED_RAW_PATH_FILES = new Set([
  path.join("src", "api", "contracts", "api-surface.generated.js"),
  path.join("src", "api", "contracts", "openapi-client.generated.js"),
  path.join("src", "api", "contracts", "openapi-contract.generated.js"),
  path.join("scripts", "generate-api-surface-contract.mjs"),
  path.join("scripts", "generate-openapi-client.mjs"),
  path.join("scripts", "generate-openapi-contract.mjs"),
]);

const extensions = new Set([".js", ".jsx", ".ts", ".tsx"]);
const violations = [];

function parseSource(source, rel) {
  try {
    return parse(source, {
      sourceType: "unambiguous",
      plugins: [
        "jsx",
        "typescript",
        "dts",
        "classProperties",
        "classPrivateProperties",
        "classPrivateMethods",
        "decorators-legacy",
        "dynamicImport",
        "importMeta",
      ],
    });
  } catch (error) {
    throw new Error(`Failed to parse ${rel}: ${error.message}`);
  }
}

function isApiPathArgument(nodePath) {
  const parent = nodePath.parentPath;
  return (
    parent?.isCallExpression() &&
    parent.node.callee?.type === "Identifier" &&
    parent.node.callee.name === "apiPath" &&
    parent.node.arguments[0] === nodePath.node
  );
}

function protectedLiteralValue(node) {
  if (node.type === "StringLiteral") return node.value;
  if (node.type === "TemplateLiteral" && node.expressions.length === 0) {
    return node.quasis[0]?.value?.cooked || node.quasis[0]?.value?.raw || "";
  }
  if (node.type === "TemplateLiteral" && node.quasis[0]?.value?.raw) {
    return node.quasis[0].value.raw;
  }
  return "";
}

function staticStringValue(node) {
  if (!node) return null;
  if (node.type === "StringLiteral") return node.value;
  if (node.type === "TemplateLiteral" && node.expressions.length === 0) {
    return node.quasis[0]?.value?.cooked || node.quasis[0]?.value?.raw || "";
  }
  return null;
}

function checkApiPathCall(nodePath, rel) {
  if (nodePath.node.callee?.type !== "Identifier") return;
  if (nodePath.node.callee.name !== "apiPath") return;

  const firstArg = nodePath.node.arguments[0];
  const value = staticStringValue(firstArg);
  const line = firstArg?.loc?.start?.line || nodePath.node.loc?.start?.line || 1;

  if (!value) {
    violations.push(`${rel}:${line}: apiPath first argument must be a static path`);
    return;
  }

  if (!Object.prototype.hasOwnProperty.call(API_SURFACE_PATHS, value)) {
    violations.push(`${rel}:${line}: apiPath target is not in generated Swagger surface: ${value}`);
  }
}

function checkLiteral(nodePath, rel) {
  const value = protectedLiteralValue(nodePath.node);
  if (!API_PATH_RE.test(value)) return;
  if (isApiPathArgument(nodePath)) return;

  const loc = nodePath.node.loc?.start;
  const line = loc?.line || 1;
  violations.push(`${rel}:${line}: ${value}`);
}

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    const rel = path.relative(frontendRoot, full);

    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "__mocks__") continue;
      walk(full);
      continue;
    }

    if (!extensions.has(path.extname(entry.name))) continue;
    if (entry.name.endsWith(".d.ts")) continue;
    if (ALLOWED_RAW_PATH_FILES.has(rel)) continue;

    const source = fs.readFileSync(full, "utf8");
    const ast = parseSource(source, rel);
    traverse(ast, {
      CallExpression(nodePath) {
        checkApiPathCall(nodePath, rel);
      },
      StringLiteral(nodePath) {
        checkLiteral(nodePath, rel);
      },
      TemplateLiteral(nodePath) {
        checkLiteral(nodePath, rel);
      },
    });
  }
}

walk(srcRoot);
walk(scriptsRoot);

if (violations.length) {
  console.error(
    [
      "Annotation/filter API paths must go through apiPath() so they are checked against the generated Swagger surface.",
      ...violations,
    ].join("\n"),
  );
  process.exit(1);
}
