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
  API_CONTRACT_EXCEPTION_STATUSES,
  API_CONTRACT_EXCEPTIONS,
} from "../src/api/contracts/api-contract-exceptions.js";

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
const MAX_CONTRACT_EXCEPTION_REGISTRY_PATHS = 0;
const MAX_CONTRACT_EXCEPTION_SURFACE_PATHS = 0;
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
      if (
        isPathRegistryCall(
          nodePath,
          new Set(["apiPath", "uncontractedApiPath"]),
        )
      )
        return;
      const value = rawPathValue(nodePath.node);
      if (value && API_PATH_RE.test(value)) {
        rawPaths.push({ value, line: nodePath.node.loc?.start?.line || 1 });
      }
    },
    TemplateLiteral(nodePath) {
      if (!isInEndpointRegistry(nodePath)) return;
      if (
        isPathRegistryCall(
          nodePath,
          new Set(["apiPath", "uncontractedApiPath"]),
        )
      )
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

function collectContractExceptionRegistryPaths() {
  const contractExceptionPaths = [];
  traverse(ast, {
    CallExpression(nodePath) {
      if (!isInEndpointRegistry(nodePath)) return;
      if (nodePath.node.callee?.type !== "Identifier") return;
      if (nodePath.node.callee.name !== "uncontractedApiPath") return;

      const [templateArg, secondArg, thirdArg] = nodePath.node.arguments;
      const value = staticStringValue(templateArg);
      const inlineReason = Boolean(staticStringValue(thirdArg || secondArg));
      contractExceptionPaths.push({
        value,
        inlineReason,
        line:
          templateArg?.loc?.start?.line || nodePath.node.loc?.start?.line || 1,
      });
    },
  });
  return contractExceptionPaths;
}

function collectApiPathRegistryPaths() {
  const apiPaths = [];
  traverse(ast, {
    CallExpression(nodePath) {
      if (!isInEndpointRegistry(nodePath)) return;
      if (nodePath.node.callee?.type !== "Identifier") return;
      if (nodePath.node.callee.name !== "apiPath") return;

      const [templateArg] = nodePath.node.arguments;
      apiPaths.push({
        value: staticStringValue(templateArg),
        line:
          templateArg?.loc?.start?.line || nodePath.node.loc?.start?.line || 1,
      });
    },
  });
  return apiPaths;
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
const apiPathPaths = collectApiPathRegistryPaths();
const invalidApiPaths = apiPathPaths.filter(
  (rawPath) =>
    !rawPath.value ||
    !API_PATH_RE.test(rawPath.value) ||
    !matchedContractTemplate(rawPath.value),
);
const contractExceptionPaths = collectContractExceptionRegistryPaths();
const contractExceptionSurfacePaths = Object.keys(API_CONTRACT_EXCEPTIONS);
const usedContractExceptionSurfacePaths = new Set(
  contractExceptionPaths.map(({ value }) => value),
);
const unusedContractExceptionSurfacePaths =
  contractExceptionSurfacePaths.filter(
    (value) => !usedContractExceptionSurfacePaths.has(value),
  );
const missingContractExceptionMetadata = contractExceptionSurfacePaths.filter(
  (value) => {
    const meta = API_CONTRACT_EXCEPTIONS[value];
    return !meta?.group || !meta?.status || !meta?.reason || !meta?.next;
  },
);
const validContractExceptionStatuses = new Set(
  Object.values(API_CONTRACT_EXCEPTION_STATUSES),
);
const invalidContractExceptionStatuses = contractExceptionSurfacePaths.filter(
  (value) => {
    const status = API_CONTRACT_EXCEPTIONS[value]?.status;
    return status && !validContractExceptionStatuses.has(status);
  },
);
const contractedContractExceptionSurfacePaths =
  contractExceptionSurfacePaths.filter((value) =>
    matchedContractTemplate(value),
  );
const invalidContractExceptionPaths = contractExceptionPaths.filter(
  (rawPath) =>
    !rawPath.value ||
    rawPath.inlineReason ||
    !API_PATH_RE.test(rawPath.value) ||
    matchedContractTemplate(rawPath.value) ||
    !Object.prototype.hasOwnProperty.call(
      API_CONTRACT_EXCEPTIONS,
      rawPath.value,
    ),
);

if (
  registryPaths.length > MAX_RAW_REGISTRY_PATHS ||
  uncontracted.length > MAX_UNMARKED_UNCONTRACTED_REGISTRY_PATHS ||
  invalidApiPaths.length ||
  contractExceptionPaths.length > MAX_CONTRACT_EXCEPTION_REGISTRY_PATHS ||
  contractExceptionSurfacePaths.length > MAX_CONTRACT_EXCEPTION_SURFACE_PATHS ||
  invalidContractExceptionPaths.length ||
  unusedContractExceptionSurfacePaths.length ||
  missingContractExceptionMetadata.length ||
  invalidContractExceptionStatuses.length ||
  contractedContractExceptionSurfacePaths.length
) {
  console.error(
    [
      "Endpoint registry contract coverage failed.",
      `Raw registry paths: ${registryPaths.length}/${MAX_RAW_REGISTRY_PATHS}`,
      `Invalid apiPath(...) calls: ${invalidApiPaths.length}`,
      `Unmarked uncontracted paths: ${uncontracted.length}/${MAX_UNMARKED_UNCONTRACTED_REGISTRY_PATHS}`,
      `Contract exception paths: ${contractExceptionPaths.length}/${MAX_CONTRACT_EXCEPTION_REGISTRY_PATHS}`,
      `Contract exception manifest paths: ${contractExceptionSurfacePaths.length}/${MAX_CONTRACT_EXCEPTION_SURFACE_PATHS}`,
      "Add the missing backend Swagger serializer/path first, then switch frontend endpoints to apiPath().",
      ...uncontracted
        .slice(0, 80)
        .map(({ line, value }) => `  - src/utils/axios.js:${line}: ${value}`),
      ...invalidApiPaths
        .slice(0, 80)
        .map(
          ({ line, value }) =>
            `  - invalid apiPath src/utils/axios.js:${line}: ${value || "<dynamic>"} (missing generated backend contract)`,
        ),
      ...invalidContractExceptionPaths
        .slice(0, 80)
        .map(
          ({ line, value, inlineReason }) =>
            `  - invalid contract exception src/utils/axios.js:${line}: ${value || "<dynamic>"} (${inlineReason ? "inline reason should move to api-contract-exceptions.js" : "missing manifest entry or now contracted"})`,
        ),
      ...unusedContractExceptionSurfacePaths
        .slice(0, 80)
        .map(
          (value) => `  - unused contract exception manifest path: ${value}`,
        ),
      ...missingContractExceptionMetadata
        .slice(0, 80)
        .map(
          (value) =>
            `  - incomplete contract exception manifest metadata: ${value}`,
        ),
      ...invalidContractExceptionStatuses
        .slice(0, 80)
        .map(
          (value) =>
            `  - invalid contract exception manifest status: ${value} (${API_CONTRACT_EXCEPTIONS[value]?.status})`,
        ),
      ...contractedContractExceptionSurfacePaths
        .slice(0, 80)
        .map(
          (value) => `  - contract exception path is now contracted: ${value}`,
        ),
    ].join("\n"),
  );
  process.exit(1);
}

console.log(
  [
    "Endpoint registry contract coverage:",
    `  apiPath calls backed by Swagger: ${apiPathPaths.length - invalidApiPaths.length}/${apiPathPaths.length}`,
    `  raw registry paths: ${registryPaths.length}/${MAX_RAW_REGISTRY_PATHS}`,
    `  contracted by Swagger: ${registryPaths.length - uncontracted.length}`,
    `  unmarked uncontracted paths: ${uncontracted.length}/${MAX_UNMARKED_UNCONTRACTED_REGISTRY_PATHS}`,
    `  contract exception paths: ${contractExceptionPaths.length}/${MAX_CONTRACT_EXCEPTION_REGISTRY_PATHS}`,
    `  contract exception manifest paths: ${contractExceptionSurfacePaths.length}/${MAX_CONTRACT_EXCEPTION_SURFACE_PATHS}`,
  ].join("\n"),
);
