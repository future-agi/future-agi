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
const jsTrivia = /\/\*[\s\S]*?\*\/|\/\/[^\n]*|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`/g;

export function taskCodeInputs(code, language = "python") {
  const python = language === "python";
  if (!code || (!python && language !== "javascript")) return null;
  const text = code.replace(python ? pythonTrivia : jsTrivia, (token) =>
    token.replace(/[^\n]/g, /^['"`]/.test(token) ? "~" : " "),
  );
  // Both executors look up evaluate first and fall back to main. A later
  // definition of the same name replaces an earlier one in the namespace.
  const definitions = python
    ? /^def[ \t]+(evaluate|main)\s*\(/gm
    : /^[ \t]*(?:async\s+)?function\s+(evaluate|main)\s*\(/gm;
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
  let args = text.slice(start, end - 1).trim();
  if (!args) return [];
  if (!python) {
    // JavaScript receives one object, not one positional argument per key.
    const object = /^\{([\s\S]*)\}$/.exec(args.replace(/\s*=\s*\{\s*\}$/, ""));
    if (!object) return null;
    args = object[1];
  }
  const inputs = [];
  for (const raw of splitTopLevelCommas(args)) {
    const param = raw.trim();
    if (!param || param.startsWith("*") || param.startsWith("...")) continue;
    // Positional-only Python parameters can't be supplied via fn(**args).
    if (param === "/") return null;
    const name = /^([A-Za-z_$][\w$]*)\s*(?=[:=]|$)/.exec(param)?.[1];
    if (!name) return null;
    if (python && name === "context") continue; // supplied by both runners
    // Both Python runners supply standard row arguments (even when missing)
    // rather than relying on their signature defaults. For a nonstandard
    // default the local runner omits the argument, the HTTP runner passes
    // None; allow an explicit override without promising identical results.
    const rowArg = python && ["input", "output", "expected"].includes(name);
    inputs.push({ name, required: rowArg || !param.includes("=") });
  }
  return inputs;
}
