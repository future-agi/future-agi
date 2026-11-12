/* eslint-env node */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";
import { collectContractExceptionCalls } from "./collect-api-contract-exceptions.mjs";

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
// The gateway console registers 35 endpoints that the generated contract does
// not list, because this API serves no Django view for them (see
// api-contract-exceptions.js); the console hides every page and control that
// would call them. Lower both caps back to 0 when their views exist, the
// contracts are regenerated and those endpoints move to apiPath().
// The 38 Datasets + Experiments prototype endpoints (PRODUCT-SPEC §6.2, plus
// R2-SPEC §6 I1, S1, IM1, plus R3 task-compare, task-compare review and the
// two R3 saved-views paths, plus the 15 R4 paths in endpoints.develop.r4:
// catalog, calls, call audio, connect machines / tokens / sessions / try,
// run activity / control / upload / rerun / paths, voice summary; dataset integration
// FE-0 removed voice try) are
// registered exceptions pending backend contracts; see
// src/api/contracts/api-contract-exceptions.js. Lower these to 0 when the
// backend ships them and the callers switch to apiPath(...).
// The workspace's decision-model listing /agentcc/decisions/models/ is in the
// generated contract now, so it is no longer counted here.
// the interface contract (the interface contract §3) adds 15 more in endpoints.develop.w4: dataset access,
// sharing policy, audit, dataset / row comments, assignments, translate,
// corrections, queue invites and review links (38 + 15 = 53).
// the interface contract (the interface contract §3.2) adds 2 more in endpoints.develop.w9: the dataset list
// query and the per-user dataset star (53 + 2 = 55).
// the interface contract implementation (w3-rows-in, the interface contract) adds 4 more: IM1 import jobs, a dataset's
// saved sources, one source, and Refresh now (55 + 4 = 59).
// the interface contract implementation (the interface contract eval versions) adds 1 more: the flip preview
// /model-hub/eval-templates/{template_id}/versions/{version}/preview/
// (59 + 1 = 60).
// the interface contract (the interface contract §3.1 the interface contract) adds 12 more in endpoints.develop.w5: custom
// result columns and their values (A); run metrics, classification, compare
// by a key and best of N (B); the tag registry, one tag, experiment tags,
// leaderboards, one leaderboard and a run's traces (C) (60 + 12 = 72).
// KH (KH-SCOPE §3.3 KH-R) adds 1 more in endpoints.develop.kh: record a run
// started with Record off (implementation, the interface contract) (72 + 1 = 73).
// the interface contract (the interface contract §3.1 the interface contract) adds 12 more in endpoints.develop.w7: decision
// trees (create, read / edit, Test on a row, a run's paths) and a
// composite's jury report (A); agreement with reviewers and Improve judge
// (B); remote scorers (create, read / edit, Test, Score a run, the job) (C)
// (73 + 12 = 85).
// HO D7 adds the 13th the interface contract path in endpoints.develop.w5: an Expression column
// on a dataset, POST add_expression_column (the interface contract) (85 + 1 = 86).
const MAX_CONTRACT_EXCEPTION_REGISTRY_PATHS = 116;
const MAX_CONTRACT_EXCEPTION_SURFACE_PATHS = 116;
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
  // A template parameter stands only for a placeholder segment; a literal
  // segment (e.g. `rows/from-results/`) is another route, not a parameter value.
  const rawSegments = withoutQuery.split("/");
  return (
    apiPathMatchers.find(
      ({ template, regex }) =>
        regex.test(concretePath) &&
        template
          .split("/")
          .every(
            (segment, index) =>
              !/^\{[^}]+\}$/.test(segment) ||
              /\$\{\}|\{[^}]+\}/.test(rawSegments[index]),
          ),
    )?.template || null
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
const contractExceptionPaths = collectContractExceptionCalls(source, {
  registryOnly: true,
});
// This consumer builds copy text, not an axios endpoint. Count its actual
// helper call and apply the same validation; do not exempt a manifest path.
const rowCopySourcePath =
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/rowApiPath.js";
const copyContractExceptionPaths = collectContractExceptionCalls(
  fs.readFileSync(path.join(frontendRoot, rowCopySourcePath), "utf8"),
).map((call) => ({ ...call, file: rowCopySourcePath }));
const contractExceptionSurfacePaths = Object.keys(API_CONTRACT_EXCEPTIONS);
const usedContractExceptionSurfacePaths = new Set(
  [...contractExceptionPaths, ...copyContractExceptionPaths].map(
    ({ value }) => value,
  ),
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
const invalidContractExceptionPaths = [
  ...contractExceptionPaths,
  ...copyContractExceptionPaths,
].filter(
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
  process.stderr.write(
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
          ({ line, value, inlineReason, file = "src/utils/axios.js" }) =>
            `  - invalid contract exception ${file}:${line}: ${value || "<dynamic>"} (${inlineReason ? "inline reason should move to api-contract-exceptions.js" : "missing manifest entry or now contracted"})`,
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
    ].join("\n") + "\n",
  );
  process.exit(1);
}

process.stdout.write(
  [
    "Endpoint registry contract coverage:",
    `  apiPath calls backed by Swagger: ${apiPathPaths.length - invalidApiPaths.length}/${apiPathPaths.length}`,
    `  raw registry paths: ${registryPaths.length}/${MAX_RAW_REGISTRY_PATHS}`,
    `  contracted by Swagger: ${registryPaths.length - uncontracted.length}`,
    `  unmarked uncontracted paths: ${uncontracted.length}/${MAX_UNMARKED_UNCONTRACTED_REGISTRY_PATHS}`,
    `  contract exception paths: ${contractExceptionPaths.length}/${MAX_CONTRACT_EXCEPTION_REGISTRY_PATHS}`,
    `  contract exception manifest paths: ${contractExceptionSurfacePaths.length}/${MAX_CONTRACT_EXCEPTION_SURFACE_PATHS}`,
  ].join("\n") + "\n",
);
