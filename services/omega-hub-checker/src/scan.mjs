/**
 * Step 6, static scan (SPEC §4.3): a TypeScript-compiler AST walk over every code file. It is a
 * tripwire, not a proof: blocks stop obvious dynamic-code and credential tricks, and flags point a
 * human reviewer at the lines that need reading. It also records the facts `observed` is built from.
 */
import ts from "typescript";
import { block, flag } from "./findings.mjs";

export const CODE = [".ts", ".mts", ".mjs", ".js", ".tsx", ".cjs", ".cts", ".jsx"];
const FLAGGED_MODULES = {
  child_process: ["child-process", "shell"], worker_threads: ["worker-threads", "workers"], net: ["raw-network", "rawNetwork"],
  tls: ["raw-network", "rawNetwork"], dgram: ["raw-network", "rawNetwork"], dns: ["raw-network", "rawNetwork"],
  http: ["raw-network", "rawNetwork"], https: ["raw-network", "rawNetwork"], http2: ["raw-network", "rawNetwork"],
};
const SENSITIVE = [".ssh", ".aws", ".gnupg", ".config", ".npmrc", ".netrc", ".docker"];
const WRITES = new Set(["writeFile", "writeFileSync", "appendFile", "appendFileSync", "copyFile", "copyFileSync", "cp", "cpSync",
  "rename", "renameSync", "createWriteStream", "symlink", "symlinkSync"]);
const TARGET_SECOND = new Set(["copyFile", "copyFileSync", "cp", "cpSync", "rename", "renameSync", "symlink", "symlinkSync"]);
const CODE_TARGET = /\.(js|mjs|cjs|ts|mts|sh|json)$/i;
const SPAWNERS = new Set(["spawn", "exec", "execFile", "fork", "spawnSync", "execSync", "execFileSync"]);
const BASE64 = /^[A-Za-z0-9+/=\s]+$/;
const HEX = /^[0-9a-fA-F\s]+$/;
const URL_LITERAL = /^https?:\/\/([^/\s:"'`]+)/i;
// Reading or writing another process's state (its fds, memory, command line), and signalling processes:
// what a submission would use against the judge (SPEC §13). The runner refuses it; the reviewer reads it.
const PROC = /^\/proc(\/|$)/;
const SIGNALLERS = new Set(["kill", "_debugProcess"]);

const moduleName = (spec) => spec.replace(/^node:/, "").split("/")[0];
const isCode = (rel) => CODE.some((ext) => rel.toLowerCase().endsWith(ext));
const literalText = (node) => (node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) ? node.text : null);
const nameOf = (expr) => (ts.isIdentifier(expr) ? expr.text : ts.isPropertyAccessExpression(expr) ? expr.name.text : null);
const isProcessEnv = (node) => node && ts.isPropertyAccessExpression(node) && node.name.text === "env" && ts.isIdentifier(node.expression) && node.expression.text === "process";
const isHomedir = (node) => node && ts.isCallExpression(node) && nameOf(node.expression) === "homedir";

function scriptKind(rel) {
  if (rel.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (rel.endsWith(".jsx")) return ts.ScriptKind.JSX;
  return /\.(ts|mts|cts)$/.test(rel) ? ts.ScriptKind.TS : ts.ScriptKind.JS;
}

/** Scan one code file. Returns `{ findings, facts }`; facts feed `observed`. */
export function scanFile(rel, text) {
  const findings = [];
  const facts = { imports: [], hosts: [], shell: [], workers: [], rawNetwork: [], filesOutside: [], envEnumeration: [], writesCode: [], keepsRunning: [] };
  const source = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true, scriptKind(rel));
  const lineOf = (node) => source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
  const at = (node) => `${rel}:${lineOf(node)}`;
  const B = (rule, node, message, fix) => findings.push(block("static-scan", rule, rel, message, { line: lineOf(node), fix }));
  const F = (rule, node, message, fact) => {
    findings.push(flag("static-scan", rule, rel, message, { line: lineOf(node) }));
    if (fact) facts[fact].push(at(node));
  };

  const importOf = (spec, node) => {
    facts.imports.push(spec);
    const mod = moduleName(spec);
    if (mod === "vm") B("vm", node, `imports ${spec}, which runs code from strings`, "remove it (C3)");
    const flagged = FLAGGED_MODULES[mod];
    if (flagged && spec.replace(/^node:/, "").split("/")[0] === mod && /^(node:)?[a-z_0-9]+(\/promises)?$/.test(spec)) {
      F(flagged[0], node, `imports ${spec}`, flagged[1]);
    }
  };

  const visit = (node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && literalText(node.moduleSpecifier) !== null) {
      importOf(literalText(node.moduleSpecifier), node);
    } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      const spec = literalText(node.moduleReference.expression);
      if (spec !== null) importOf(spec, node);
    } else if (ts.isNewExpression(node) && nameOf(node.expression) === "Function") {
      B("new-function", node, "new Function() runs code from a string", "remove it (C3)");
    } else if (ts.isCallExpression(node)) {
      visitCall(node);
    } else if ((ts.isSpreadAssignment(node) || ts.isSpreadElement(node)) && isProcessEnv(node.expression)) {
      F("env-enumeration", node, "spreads process.env", "envEnumeration");
    } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      visitLiteral(node);
    } else if (ts.isTemplateExpression(node) && PROC.test(node.head.text)) {
      F("proc-access", node, `a path under /proc: ${node.head.text}…`);
    }
    ts.forEachChild(node, visit);
  };

  const visitLiteral = (node) => {
    const value = node.text;
    if (value.length > 2048 && (BASE64.test(value) || HEX.test(value))) B("encoded-blob", node, `a ${value.length}-character string literal that decodes as base64 or hex`, "ship readable source (C4)");
    const host = URL_LITERAL.exec(value);
    if (host && !/^(localhost|127\.0\.0\.1|\[::1\])$/i.test(host[1])) facts.hosts.push(host[1].toLowerCase());
    if (PROC.test(value)) F("proc-access", node, `a path under /proc: ${value}`);
    if (/^~\//.test(value) || SENSITIVE.some((dir) => value.includes(`/${dir}/`) || value.endsWith(`/${dir}`))) {
      if (/^~\//.test(value) && SENSITIVE.some((dir) => value.startsWith(`~/${dir}`))) B("home-secrets", node, `reads or writes ${value}`, "never touch credential folders (C5)");
      else if (/^~\//.test(value)) F("home-access", node, `a path under the home directory: ${value}`, "filesOutside");
    }
  };

  const visitCall = (node) => {
    const callee = node.expression;
    const name = nameOf(callee);
    const [first, second] = node.arguments;
    if (callee.kind === ts.SyntaxKind.ImportKeyword) {
      const spec = literalText(first);
      if (spec === null) B("dynamic-import", node, "import() with an argument that is not a string literal", "import modules by name (C3)");
      else importOf(spec, node);
      return;
    }
    if (ts.isIdentifier(callee) && callee.text === "require") {
      const spec = literalText(first);
      if (spec === null) B("dynamic-require", node, "require() with an argument that is not a string literal", "require modules by name (C3)");
      else importOf(spec, node);
      return;
    }
    if (ts.isIdentifier(callee) && (callee.text === "eval" || callee.text === "Function")) B(callee.text === "eval" ? "eval" : "new-function", node, `${callee.text}() runs code from a string`, "remove it (C3)");
    if (name === "createRequire") B("create-require", node, "createRequire() loads modules outside the import graph", "use import (C3)");
    if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) && callee.expression.text === "process" && ["binding", "dlopen"].includes(callee.name.text)) {
      B("native-code", node, `process.${callee.name.text}() loads native code`, "remove it");
    }
    if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) && callee.expression.text === "process" && SIGNALLERS.has(callee.name.text)) {
      F("signals-processes", node, `process.${callee.name.text}() signals another process`);
    }
    if (name === "fetch") {
      const url = literalText(first);
      if (url === null) F("fetch-dynamic", node, "fetch() to a URL that is not a literal", "hosts");
    }
    if (name === "setInterval") F("keeps-running", node, "setInterval keeps the process running", "keepsRunning");
    if (name === "unref") F("keeps-running", node, ".unref() lets work outlive the main flow", "keepsRunning");
    if (name && SPAWNERS.has(name)) {
      const options = node.arguments.find((arg) => ts.isObjectLiteralExpression(arg));
      const detached = options?.properties.some((prop) => ts.isPropertyAssignment(prop) && nameOf(prop.name) === "detached" && prop.initializer.kind === ts.SyntaxKind.TrueKeyword);
      if (detached) F("keeps-running", node, `${name}() with detached: true`, "keepsRunning");
    }
    if (name && WRITES.has(name)) {
      const target = TARGET_SECOND.has(name) ? second : first;
      const text = literalText(target);
      if (text === null || CODE_TARGET.test(text)) F("writes-code", node, `${name}() writes ${text === null ? "a computed path" : text}`, "writesCode");
    }
    if (ts.isPropertyAccessExpression(callee) && ["keys", "entries", "values"].includes(callee.name.text) && nameOf(callee.expression) === "Object" && isProcessEnv(first)) {
      F("env-enumeration", node, `Object.${callee.name.text}(process.env)`, "envEnumeration");
    }
    if (ts.isPropertyAccessExpression(callee) && callee.name.text === "stringify" && nameOf(callee.expression) === "JSON" && isProcessEnv(first)) {
      F("env-enumeration", node, "JSON.stringify(process.env)", "envEnumeration");
    }
    if (["join", "resolve"].includes(name ?? "") && node.arguments.some(isHomedir)) {
      const parts = node.arguments.map(literalText).filter((part) => part !== null);
      if (parts.some((part) => SENSITIVE.some((dir) => part === dir || part.startsWith(`${dir}/`)))) B("home-secrets", node, `joins the home directory with ${parts.join("/")}`, "never touch credential folders (C5)");
      else F("home-access", node, "a path under the home directory", "filesOutside");
    } else if (isHomedir(node)) {
      facts.filesOutside.push(at(node));
    }
  };

  visit(source);
  const lines = text.split("\n");
  const long = lines.findIndex((line) => line.length > 2000);
  if (long >= 0) findings.push(block("static-scan", "minified", rel, `line ${long + 1} is ${lines[long].length} characters long: minified or generated code`, { line: long + 1, fix: "ship readable source (C4)" }));
  return { findings, facts };
}

/** Scan every code file, the README's images and the manifest's browser requirement. */
export function scanEntry(files, manifest) {
  const findings = [];
  const facts = { imports: [], hosts: [], shell: [], workers: [], rawNetwork: [], filesOutside: [], envEnumeration: [], writesCode: [], keepsRunning: [] };
  for (const [rel, data] of files) {
    if (!isCode(rel)) continue;
    const result = scanFile(rel, data.toString("utf8"));
    findings.push(...result.findings);
    for (const key of Object.keys(facts)) facts[key].push(...result.facts[key]);
  }
  const readme = files.get("README.md")?.toString("utf8") ?? "";
  for (const match of readme.matchAll(/!\[[^\]]*\]\((https?:[^)\s]+)|<img[^>]+src=["'](https?:[^"']+)/gi)) {
    findings.push(flag("static-scan", "remote-image", "README.md", `a remote image: ${match[1] ?? match[2]}`, { line: readme.slice(0, match.index).split("\n").length }));
  }
  if (manifest?.requires?.chromium) findings.push(flag("static-scan", "chromium", "omega.hub.json", "requires.chromium: the entry drives a browser"));
  facts.hosts = [...new Set(facts.hosts)].sort();
  return { findings, facts };
}
