/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(frontendRoot, "..");
const outputPath = path.join(
  repoRoot,
  "api_contracts",
  "openapi",
  "runtime-management-api-contract-debt.generated.json",
);

const STRICT_VIEW_TARGETS = [
  path.join(repoRoot, "futureagi", "accounts", "views"),
  path.join(repoRoot, "futureagi", "model_hub", "views"),
  path.join(repoRoot, "futureagi", "tracer", "views"),
];
const PRODUCT_VIEW_TARGETS = [
  path.join(repoRoot, "futureagi", "accounts", "views"),
  path.join(repoRoot, "futureagi", "agent_playground", "views"),
  path.join(repoRoot, "futureagi", "agentcc", "views"),
  path.join(repoRoot, "futureagi", "integrations", "views"),
  path.join(repoRoot, "futureagi", "mcp_server", "views"),
  path.join(repoRoot, "futureagi", "model_hub", "views"),
  path.join(repoRoot, "futureagi", "sdk", "views"),
  path.join(repoRoot, "futureagi", "simulate", "views"),
  path.join(repoRoot, "futureagi", "tfc", "views"),
  path.join(repoRoot, "futureagi", "tracer", "views"),
  path.join(repoRoot, "futureagi", "ai_tools", "views.py"),
  path.join(repoRoot, "futureagi", "saml2_auth", "views.py"),
].filter((target) => fs.existsSync(target));
const HTTP_DECORATOR_RE =
  /^\s*@(swagger_auto_schema|validated_request|validated_api_request)\s*\((?<inline>.*)$/;
const DEF_RE = /^\s*def\s+(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(/;
const CLASS_RE = /^\s*class\s+(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*[(:]/;
const BROAD_REQUEST_SERIALIZERS = new Set(["AccountsJSONRequestSerializer"]);

function walkPythonFiles(dir) {
  const files = [];
  const entries = fs
    .readdirSync(dir, { withFileTypes: true })
    .sort((a, b) => a.name.localeCompare(b.name));
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (["__pycache__", "migrations"].includes(entry.name)) continue;
      files.push(...walkPythonFiles(full));
    } else if (entry.isFile() && entry.name.endsWith(".py")) {
      files.push(full);
    }
  }
  return files;
}

function walkPythonTarget(target) {
  const stat = fs.statSync(target);
  if (stat.isDirectory()) return walkPythonFiles(target);
  if (stat.isFile() && target.endsWith(".py")) return [target];
  return [];
}

function countParenDelta(value) {
  let delta = 0;
  let inString = null;
  let escaped = false;
  for (const char of value) {
    if (escaped) {
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (inString) {
      if (char === inString) inString = null;
      continue;
    }
    if (char === "'" || char === '"') {
      inString = char;
    } else if (char === "(") {
      delta += 1;
    } else if (char === ")") {
      delta -= 1;
    }
  }
  return delta;
}

function readDecorator(lines, startIndex) {
  const firstLine = lines[startIndex];
  const match = firstLine.match(HTTP_DECORATOR_RE);
  if (!match) return null;

  const decorator = match[1];
  const collected = [firstLine];
  let parenDepth = countParenDelta(firstLine);
  let index = startIndex + 1;
  while (parenDepth > 0 && index < lines.length) {
    collected.push(lines[index]);
    parenDepth += countParenDelta(lines[index]);
    index += 1;
  }

  return {
    decorator,
    text: collected.join("\n"),
    startLine: startIndex + 1,
    nextIndex: index,
  };
}

function lineIndent(line) {
  return line.match(/^(\s*)/)?.[1].length || 0;
}

function nextFunctionInfo(lines, startIndex) {
  for (let i = startIndex; i < lines.length; i += 1) {
    const match = lines[i].match(DEF_RE);
    if (match) {
      return {
        name: match.groups.name,
        lineIndex: i,
        indent: lineIndent(lines[i]),
      };
    }
    const trimmed = lines[i].trim();
    if (!trimmed) continue;
    if (trimmed.startsWith("@")) {
      let parenDepth = countParenDelta(lines[i]);
      while (parenDepth > 0 && i + 1 < lines.length) {
        i += 1;
        parenDepth += countParenDelta(lines[i]);
      }
      continue;
    }
    return null;
  }
  return null;
}

function enclosingClassName(lines, functionInfo) {
  if (!functionInfo) return null;
  for (let i = functionInfo.lineIndex - 1; i >= 0; i -= 1) {
    if (lineIndent(lines[i]) >= functionInfo.indent) continue;
    const match = lines[i].match(CLASS_RE);
    if (match) return match.groups.name;
  }
  return null;
}

function serializerNames(text) {
  const names = new Set();
  const patterns = [
    /request_body\s*=\s*([A-Za-z_][A-Za-z0-9_]*)/g,
    /query_serializer\s*=\s*([A-Za-z_][A-Za-z0-9_]*)/g,
    /request_serializer\s*=\s*([A-Za-z_][A-Za-z0-9_]*)/g,
  ];
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      names.add(match[1]);
    }
  }
  return [...names].sort();
}

function decoratorUsesRuntimeValidation(record) {
  return ["validated_request", "validated_api_request"].includes(
    record?.decorator,
  );
}

function decoratorHasInputContract(record) {
  if (!record) return false;
  return (
    /\brequest_body\s*=/.test(record.text) ||
    /\bquery_serializer\s*=/.test(record.text) ||
    /\brequest_serializer\s*=/.test(record.text)
  );
}

function swaggerDecoratorHasRuntimeValidation(record) {
  if (record?.decorator !== "swagger_auto_schema") return false;
  return (
    /\bruntime_request_validation\s*=\s*True\b/.test(record.text) ||
    /\bruntime_response_validation\s*=\s*True\b/.test(record.text)
  );
}

function analyzeFile(filePath) {
  const rel = path.relative(repoRoot, filePath);
  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  const decorators = [];

  for (let i = 0; i < lines.length; i += 1) {
    const record = readDecorator(lines, i);
    if (!record) continue;
    const functionInfo = nextFunctionInfo(lines, record.nextIndex);
    const className = enclosingClassName(lines, functionInfo);
    decorators.push({
      ...record,
      className,
      functionName: functionInfo?.name || null,
      serializers: serializerNames(record.text),
      rel,
    });
    i = record.nextIndex - 1;
  }

  return decorators;
}

function analyzeTargets(targets) {
  const decorators = targets
    .flatMap((target) => walkPythonTarget(target).flatMap(analyzeFile))
    .sort(compareDecoratorRecords);
  const validated = decorators.filter(decoratorUsesRuntimeValidation);
  const directSwagger = decorators.filter(
    (record) =>
      record.decorator === "swagger_auto_schema" &&
      !swaggerDecoratorHasRuntimeValidation(record),
  );
  const runtimeInputContractKeys = new Set(
    validated
      .filter(decoratorHasInputContract)
      .map(
        (record) =>
          `${record.rel}:${record.className || ""}.${record.functionName || ""}`,
      ),
  );
  const docOnlyInputContracts = directSwagger.filter(
    (record) =>
      decoratorHasInputContract(record) &&
      !runtimeInputContractKeys.has(
        `${record.rel}:${record.className || ""}.${record.functionName || ""}`,
      ),
  );
  const broadRequestContracts = decorators.filter((record) =>
    record.serializers.some((name) => BROAD_REQUEST_SERIALIZERS.has(name)),
  );

  return {
    decorators,
    validated,
    directSwagger,
    docOnlyInputContracts,
    broadRequestContracts,
  };
}

function formatDecorators(records) {
  return records.map((record) => ({
    path: record.rel,
    line: record.startLine,
    class: record.className,
    function: record.functionName,
    serializers: record.serializers,
  }));
}

function compareDecoratorRecords(a, b) {
  return (
    a.rel.localeCompare(b.rel) ||
    a.startLine - b.startLine ||
    (a.className || "").localeCompare(b.className || "") ||
    (a.functionName || "").localeCompare(b.functionName || "") ||
    a.decorator.localeCompare(b.decorator)
  );
}

function formatSummary(result) {
  return {
    runtime_backed_validated_request_decorators: result.validated.length,
    direct_swagger_auto_schema_decorators: result.directSwagger.length,
    doc_only_input_contract_decorators: result.docOnlyInputContracts.length,
    broad_request_contract_decorators: result.broadRequestContracts.length,
  };
}

const strictResult = analyzeTargets(STRICT_VIEW_TARGETS);
const appWideResult = analyzeTargets(PRODUCT_VIEW_TARGETS);

const report = {
  generated_from: STRICT_VIEW_TARGETS.map((target) =>
    path.relative(repoRoot, target),
  ),
  app_wide_generated_from: PRODUCT_VIEW_TARGETS.map((target) =>
    path.relative(repoRoot, target),
  ),
  summary: formatSummary(strictResult),
  app_wide_summary: formatSummary(appWideResult),
  doc_only_input_contract_decorators: formatDecorators(
    strictResult.docOnlyInputContracts,
  ),
  broad_request_contract_decorators: formatDecorators(
    strictResult.broadRequestContracts,
  ),
  app_wide_doc_only_input_contract_decorators: formatDecorators(
    appWideResult.docOnlyInputContracts,
  ),
  app_wide_broad_request_contract_decorators: formatDecorators(
    appWideResult.broadRequestContracts,
  ),
};

const nextJson = `${JSON.stringify(report, null, 2)}\n`;

if (process.argv.includes("--check")) {
  const current = fs.existsSync(outputPath)
    ? fs.readFileSync(outputPath, "utf8")
    : "";
  if (current !== nextJson) {
    console.error(
      "Runtime Management API contract debt report is stale. Run yarn contracts:generate.",
    );
    process.exit(1);
  }
  console.log("Runtime Management API contract debt report is up to date.");
} else {
  fs.writeFileSync(outputPath, nextJson);
  console.log(
    `Runtime Management API contract debt report written to ${path.relative(
      repoRoot,
      outputPath,
    )}.`,
  );
}
