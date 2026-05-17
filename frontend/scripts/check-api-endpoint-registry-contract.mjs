/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";

import {
  API_SURFACE_CONTRACT,
  API_SURFACE_PATHS,
} from "../src/api/contracts/api-surface.generated.js";
import {
  LEGACY_API_STATUSES,
  LEGACY_API_SURFACE,
} from "../src/api/contracts/legacy-api-surface.js";

const traverse = traverseModule.default;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const endpointRegistryPath = path.join(
  frontendRoot,
  "src",
  "utils",
  "axios.js",
);
const MAX_UNMARKED_UNCONTRACTED_REGISTRY_PATHS = 0;
const MAX_RAW_REGISTRY_PATHS = 0;
const MAX_LEGACY_REGISTRY_PATHS = 48;
const MAX_LEGACY_SURFACE_PATHS = 44;
const MANAGEMENT_API_GROUPS = Object.keys(API_SURFACE_CONTRACT.groups)
  .filter((groupName) => groupName !== "root")
  .sort();
const API_PATH_RE = new RegExp(
  `^/(?:${MANAGEMENT_API_GROUPS.map((groupName) =>
    groupName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
  ).join("|")})(?:/|$)`,
);

const source = fs.readFileSync(endpointRegistryPath, "utf8");
const ast = parse(source, {
  sourceType: "module",
  plugins: ["jsx", "typescript"],
});

const apiPathTemplates = Object.keys(API_SURFACE_PATHS);
const apiPathMatchers = apiPathTemplates.map((template) => ({
  template,
  regex: new RegExp(
    `^${template
      .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
      .replace(/\\\{[^}]+\\\}/g, "[^/]+")}$`,
  ),
}));

function rawPathValue(node) {
  if (node.type === "StringLiteral") return node.value;
  if (node.type !== "TemplateLiteral") return null;
  return node.quasis
    .map((quasi, index) => {
      const raw = quasi.value.raw || "";
      return index < node.expressions.length ? `${raw}\${}` : raw;
    })
    .join("");
}

function isInEndpointRegistry(nodePath) {
  return Boolean(
    nodePath.findParent(
      (parentPath) =>
        parentPath.isVariableDeclarator() &&
        parentPath.node.id?.type === "Identifier" &&
        parentPath.node.id.name === "endpoints",
    ),
  );
}

function isPathRegistryCall(nodePath, calleeNames) {
  const parent = nodePath.parentPath;
  return (
    parent?.isCallExpression() &&
    parent.node.callee?.type === "Identifier" &&
    calleeNames.has(parent.node.callee.name) &&
    parent.node.arguments[0] === nodePath.node
  );
}

function matchedContractTemplate(rawValue) {
  if (Object.prototype.hasOwnProperty.call(API_SURFACE_PATHS, rawValue)) {
    return rawValue;
  }
  const hasPlaceholder =
    rawValue.includes("${}") || /\{[^}]+\}/.test(rawValue.split("?")[0]);
  if (!hasPlaceholder) return null;
  const withoutQuery = rawValue.split("?")[0];
  const concretePath = withoutQuery.replace(/\$\{\}/g, "placeholder");
  return (
    apiPathMatchers.find(({ regex }) => regex.test(concretePath))?.template ||
    null
  );
}

function collectRawRegistryPaths() {
  const rawPaths = [];
  traverse(ast, {
    StringLiteral(nodePath) {
      if (!isInEndpointRegistry(nodePath)) return;
      if (isPathRegistryCall(nodePath, new Set(["apiPath", "legacyApiPath"])))
        return;
      const value = rawPathValue(nodePath.node);
      if (value && API_PATH_RE.test(value)) {
        rawPaths.push({ value, line: nodePath.node.loc?.start?.line || 1 });
      }
    },
    TemplateLiteral(nodePath) {
      if (!isInEndpointRegistry(nodePath)) return;
      if (isPathRegistryCall(nodePath, new Set(["apiPath", "legacyApiPath"])))
        return;
      const value = rawPathValue(nodePath.node);
      if (value && API_PATH_RE.test(value)) {
        rawPaths.push({ value, line: nodePath.node.loc?.start?.line || 1 });
      }
    },
  });
  return rawPaths;
}

function staticStringValue(node) {
  if (node?.type === "StringLiteral") return node.value;
  if (node?.type === "TemplateLiteral" && node.expressions.length === 0) {
    return node.quasis[0]?.value?.cooked || node.quasis[0]?.value?.raw || "";
  }
  return "";
}

function collectLegacyRegistryPaths() {
  const legacyPaths = [];
  traverse(ast, {
    CallExpression(nodePath) {
      if (!isInEndpointRegistry(nodePath)) return;
      if (nodePath.node.callee?.type !== "Identifier") return;
      if (nodePath.node.callee.name !== "legacyApiPath") return;

      const [templateArg, secondArg, thirdArg] = nodePath.node.arguments;
      const value = staticStringValue(templateArg);
      const inlineReason = Boolean(staticStringValue(thirdArg || secondArg));
      legacyPaths.push({
        value,
        inlineReason,
        line:
          templateArg?.loc?.start?.line || nodePath.node.loc?.start?.line || 1,
      });
    },
  });
  return legacyPaths;
}

const rawPathsByValue = new Map();
for (const rawPath of collectRawRegistryPaths()) {
  if (!rawPathsByValue.has(rawPath.value))
    rawPathsByValue.set(rawPath.value, rawPath);
}

const registryPaths = [...rawPathsByValue.values()].map((rawPath) => ({
  ...rawPath,
  contractTemplate: matchedContractTemplate(rawPath.value),
}));
const uncontracted = registryPaths.filter(
  (rawPath) => !rawPath.contractTemplate,
);
const legacyPaths = collectLegacyRegistryPaths();
const legacySurfacePaths = Object.keys(LEGACY_API_SURFACE);
const usedLegacySurfacePaths = new Set(legacyPaths.map(({ value }) => value));
const unusedLegacySurfacePaths = legacySurfacePaths.filter(
  (value) => !usedLegacySurfacePaths.has(value),
);
const missingLegacyMetadata = legacySurfacePaths.filter((value) => {
  const meta = LEGACY_API_SURFACE[value];
  return !meta?.group || !meta?.status || !meta?.reason || !meta?.next;
});
const validLegacyStatuses = new Set(Object.values(LEGACY_API_STATUSES));
const invalidLegacyStatuses = legacySurfacePaths.filter((value) => {
  const status = LEGACY_API_SURFACE[value]?.status;
  return status && !validLegacyStatuses.has(status);
});
const contractedLegacySurfacePaths = legacySurfacePaths.filter((value) =>
  matchedContractTemplate(value),
);
const invalidLegacyPaths = legacyPaths.filter(
  (rawPath) =>
    !rawPath.value ||
    rawPath.inlineReason ||
    !API_PATH_RE.test(rawPath.value) ||
    matchedContractTemplate(rawPath.value) ||
    !Object.prototype.hasOwnProperty.call(LEGACY_API_SURFACE, rawPath.value),
);

if (
  registryPaths.length > MAX_RAW_REGISTRY_PATHS ||
  uncontracted.length > MAX_UNMARKED_UNCONTRACTED_REGISTRY_PATHS ||
  legacyPaths.length > MAX_LEGACY_REGISTRY_PATHS ||
  legacySurfacePaths.length > MAX_LEGACY_SURFACE_PATHS ||
  invalidLegacyPaths.length ||
  unusedLegacySurfacePaths.length ||
  missingLegacyMetadata.length ||
  invalidLegacyStatuses.length ||
  contractedLegacySurfacePaths.length
) {
  console.error(
    [
      "Endpoint registry contract coverage failed.",
      `Raw registry paths: ${registryPaths.length}/${MAX_RAW_REGISTRY_PATHS}`,
      `Unmarked uncontracted paths: ${uncontracted.length}/${MAX_UNMARKED_UNCONTRACTED_REGISTRY_PATHS}`,
      `Marked legacy paths: ${legacyPaths.length}/${MAX_LEGACY_REGISTRY_PATHS}`,
      `Legacy surface manifest paths: ${legacySurfacePaths.length}/${MAX_LEGACY_SURFACE_PATHS}`,
      "Add the missing backend Swagger serializer/path first, switch contracted endpoints to apiPath(), or register genuinely deprecated endpoints in legacy-api-surface.js and use legacyApiPath(path).",
      ...uncontracted
        .slice(0, 80)
        .map(({ line, value }) => `  - src/utils/axios.js:${line}: ${value}`),
      ...invalidLegacyPaths
        .slice(0, 80)
        .map(
          ({ line, value, inlineReason }) =>
            `  - invalid legacy src/utils/axios.js:${line}: ${value || "<dynamic>"} (${inlineReason ? "inline reason should move to legacy-api-surface.js" : "missing manifest entry or now contracted"})`,
        ),
      ...unusedLegacySurfacePaths
        .slice(0, 80)
        .map((value) => `  - unused legacy manifest path: ${value}`),
      ...missingLegacyMetadata
        .slice(0, 80)
        .map((value) => `  - incomplete legacy manifest metadata: ${value}`),
      ...invalidLegacyStatuses
        .slice(0, 80)
        .map(
          (value) =>
            `  - invalid legacy manifest status: ${value} (${LEGACY_API_SURFACE[value]?.status})`,
        ),
      ...contractedLegacySurfacePaths
        .slice(0, 80)
        .map((value) => `  - legacy manifest path is now contracted: ${value}`),
    ].join("\n"),
  );
  process.exit(1);
}

console.log(
  [
    "Endpoint registry contract coverage:",
    `  raw registry paths: ${registryPaths.length}/${MAX_RAW_REGISTRY_PATHS}`,
    `  contracted by Swagger: ${registryPaths.length - uncontracted.length}`,
    `  unmarked uncontracted paths: ${uncontracted.length}/${MAX_UNMARKED_UNCONTRACTED_REGISTRY_PATHS}`,
    `  marked legacy paths: ${legacyPaths.length}/${MAX_LEGACY_REGISTRY_PATHS}`,
    `  legacy surface manifest paths: ${legacySurfacePaths.length}/${MAX_LEGACY_SURFACE_PATHS}`,
  ].join("\n"),
);
