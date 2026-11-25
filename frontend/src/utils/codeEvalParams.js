import { parse } from "acorn";

// Parser for the user-typed `evaluate(...)` signature in code evals.
// Used by the variable-mapping UI so new params surface live as the user types.

const RESERVED_CODE_PARAMS = new Set(["context", "self", "cls"]);

// Depth-aware split on `,` so generics like `Dict[str, int]` and nested
// destructuring patterns survive intact.
const splitTopLevelCommas = (raw) => {
  const parts = [];
  let depth = 0;
  let buf = "";
  for (let i = 0; i < raw.length; i += 1) {
    const ch = raw[i];
    if (ch === "(" || ch === "[" || ch === "{") depth += 1;
    else if (ch === ")" || ch === "]" || ch === "}") depth -= 1;
    if (ch === "," && depth === 0) {
      parts.push(buf);
      buf = "";
    } else {
      buf += ch;
    }
  }
  if (buf.trim()) parts.push(buf);
  return parts;
};

// Pull mappable parameter names out of an `evaluate(...)` signature. Supports:
//   • Python: `def evaluate(input: Any, output: Any, ...)`
//   • JavaScript: `function evaluate({ input, output, ...kwargs })`
export const extractCodeEvaluateParams = (code, language) => {
  if (!code) return [];

  let raw;
  if (!language || language === "python") {
    const m = code.match(/def\s+evaluate\s*\(([\s\S]*?)\)/);
    if (!m) return [];
    // Strip Python line comments (`# ...`) so inline-doc params parse cleanly
    // when the signature spans multiple lines.
    raw = m[1].replace(/#[^\n]*/g, "");
  } else if (language === "javascript") {
    // JS template destructures a single object arg:
    //   function evaluate({ input, output, expected, ...kwargs })
    const m = code.match(/function\s+evaluate\s*\(\s*\{([\s\S]*?)\}\s*\)/);
    if (!m) return [];
    // Strip JS line and block comments inside the destructuring pattern.
    raw = m[1].replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  } else {
    return [];
  }

  const names = [];
  for (const part of splitTopLevelCommas(raw)) {
    const p = part.trim();
    if (!p) continue;
    if (p.startsWith("*") || p.startsWith("...")) continue;
    // Strip type annotation (`: Any`), default value (`= 1`), and destructuring
    // rename (`foo: bar`) — first token before any of `:=` is the bound name.
    const name = p.split(/[:=]/)[0].trim();
    if (!name) continue;
    if (RESERVED_CODE_PARAMS.has(name)) continue;
    names.push(name);
  }
  return names;
};

// Task bindings must distinguish a verified empty entry point from code we
// can't describe. Mask comments and string contents before reading syntax;
// neither a docstring nor a commented example is an executable definition.
const pythonTrivia = /'''[\s\S]*?'''|"""[\s\S]*?"""|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|#[^\n]*/g;
const nonCallableInitializers = new Set([
  "ObjectExpression", "ArrayExpression", "Literal", "TemplateLiteral",
]);

// Both JS runners pass one object to the top-level evaluate (or main).
// Read the program's declarations, never nested declarations or source text.
function javascriptTaskInputs(code) {
  let body;
  try {
    ({ body } = parse(code, { ecmaVersion: "latest", sourceType: "script" }));
  } catch {
    return null;
  }
  const entries = new Map();
  for (const node of body) {
    if (node.type === "FunctionDeclaration") entries.set(node.id.name, node);
    if (node.type === "VariableDeclaration") {
      for (const { id, init } of node.declarations) {
        if (id.type !== "Identifier" || !init) continue;
        // The runners test typeof evaluate before trying main. A constant
        // literal cannot become callable; a factory, alias or mutable
        // binding is not proof of that, so keep its unknown AST node.
        const nonCallable = node.kind === "const" && nonCallableInitializers.has(init.type);
        entries.set(id.name, nonCallable ? null : init);
      }
    }
  }
  const fn = entries.get("evaluate") ?? entries.get("main");
  if (!fn?.params) return null;
  if (!fn.params.length) return [];
  const [arg] = fn.params;
  const pattern = arg.type === "AssignmentPattern" ? arg.left : arg;
  if (fn.params.length !== 1 || pattern.type !== "ObjectPattern") return null;
  const inputs = [];
  for (const property of pattern.properties) {
    if (property.type === "RestElement") continue;
    const key = property.key;
    const name = key.type === "Literal" ? key.value : !property.computed && key.name;
    if (typeof name !== "string") return null;
    // settings: {cutoff = 0.5} still requires the settings object. Only an
    // assignment on the property itself supplies a default for that key.
    inputs.push({ name, required: property.value.type !== "AssignmentPattern" });
  }
  return inputs;
}

export function taskCodeInputs(code, language = "python") {
  if (!code) return null;
  if (language === "javascript") return javascriptTaskInputs(code);
  if (language !== "python") return null;
  const text = code.replace(pythonTrivia, (token) =>
    token.replace(/[^\n]/g, /^['"]/.test(token) ? "~" : " "),
  );
  const definitions = /^def[ \t]+(evaluate|main)\s*\(/gm;
  const starts = {};
  for (const m of text.matchAll(definitions)) {
    starts[m[1]] = m.index + m[0].length;
  }
  const start = starts.evaluate ?? starts.main;
  if (start === undefined) return null;
  let end = start;
  let depth = 1;
  for (; end < text.length && depth; end++) {
    if (text[end] === "(") depth++;
    else if (text[end] === ")") depth--;
  }
  if (depth) return null;
  const args = text.slice(start, end - 1).trim();
  const inputs = [];
  for (const raw of splitTopLevelCommas(args)) {
    const param = raw.trim();
    if (!param || param.startsWith("*") || param.startsWith("...")) continue;
    // Positional-only Python parameters can't be supplied via fn(**args).
    if (param === "/") return null;
    const name = /^([A-Za-z_$][\w$]*)\s*(?=[:=]|$)/.exec(param)?.[1];
    if (!name) return null;
    if (name === "context") continue; // supplied by both runners
    // Both Python runners supply standard row arguments (even when missing)
    // rather than relying on their signature defaults. For a nonstandard
    // default the local runner omits the argument, the HTTP runner passes
    // None; allow an explicit override without promising identical results.
    const rowArg = ["input", "output", "expected"].includes(name);
    inputs.push({ name, required: rowArg || !param.includes("=") });
  }
  return inputs;
}
