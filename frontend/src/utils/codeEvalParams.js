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
    raw = m[1]
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/[^\n]*/g, "");
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
