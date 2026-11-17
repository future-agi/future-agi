// The names inside disclosure prose: an environment variable
// (OPENAI_API_KEY), a host (api.openai.com), a file path or URL
// (~/.omega/config.json), a module or key name (node:fs). The panel sets each
// in the code face on one line: a line never breaks inside a name.
//
// A word is a name when a "_", "." or ":" sits between two of its letters or
// digits, when it holds "://" or two or more "/", or when it opens a path or
// a variable ("/tmp", "~/", "./", ".env", "$HOME", "@scope/pkg"). Plain
// English stays prose: "and/or", "e.g.", "read-only", "10:30" and "1.5" are
// not names. Brackets, quotes and sentence punctuation around a name are not
// part of it.

const OPENING = /^[("'`“‘]+/;
const CLOSING = /[)"'`,;:!?.…”’]+$/;
const NUMBER = /^[+-]?[\d.,:%]+$/;
const ABBREVIATION = /^(?:[A-Za-z]\.)+[A-Za-z]?$/;
const NAME_SHAPES = [
  /[A-Za-z0-9]_|_[A-Za-z0-9]/,
  /[A-Za-z]\.[A-Za-z0-9]|[A-Za-z0-9]\.[A-Za-z]/,
  /[A-Za-z0-9]:[A-Za-z0-9]/,
  /:\/\//,
  /[^/]\/[^/]+\/[^/]/,
  /^(?:\/|~\/|\.\.?\/|\.[A-Za-z_]|\$[A-Za-z_{]|@[A-Za-z0-9])/,
];

// The punctuation right against a name: an opening bracket or quote just
// before it, and the closing punctuation just after it.
const BEFORE_NAME = /[("'`“‘]+$/;
const AFTER_NAME = /^[)"'`,;:!?.…”’]+/;

function isName(word) {
  if (!/[A-Za-z0-9]/.test(word)) return false;
  if (NUMBER.test(word) || ABBREVIATION.test(word)) return false;
  return NAME_SHAPES.some((shape) => shape.test(word));
}

// The text as runs of prose and names, in order; joined, the runs give the
// text back exactly.
function disclosureRuns(text) {
  const runs = [];
  const add = (value, name) => {
    if (!value) return;
    const last = runs[runs.length - 1];
    if (!name && last && !last.name) last.text += value;
    else runs.push({ text: value, name });
  };
  for (const word of String(text ?? "").split(/(\s+)/)) {
    const opening = word.match(OPENING)?.[0] || "";
    const rest = word.slice(opening.length);
    const closing = rest.match(CLOSING)?.[0] || "";
    const core = rest.slice(0, rest.length - closing.length);
    if (core && isName(core)) {
      add(opening, false);
      add(core, true);
      add(closing, false);
    } else {
      add(word, false);
    }
  }
  return runs;
}

// The text as prose (`text`) and names (`name`), each name with the
// punctuation right against it (`before`, `after`: "(" and ");" around
// "(OPENAI_API_KEY);"), so the panel keeps them on the name's line. Joined,
// the parts give the text back exactly.
export function disclosureParts(text) {
  const runs = disclosureRuns(text);
  const parts = [];
  let before = "";
  runs.forEach((run, index) => {
    if (run.name) {
      parts.push({ name: run.text, before, after: "" });
      before = "";
      return;
    }
    let prose = run.text;
    const last = parts[parts.length - 1];
    if (last?.name) {
      last.after = prose.match(AFTER_NAME)?.[0] || "";
      prose = prose.slice(last.after.length);
    }
    if (runs[index + 1]?.name) {
      before = prose.match(BEFORE_NAME)?.[0] || "";
      prose = prose.slice(0, prose.length - before.length);
    }
    if (prose) parts.push({ text: prose });
  });
  return parts;
}

// A name that starts with a letter or a digit: not a path ("~/", "./",
// "/tmp") or a variable ("$HOME").
const OWN_START = /^[A-Za-z0-9]/;

const wholeName = (part) => OWN_START.test(part) && isName(part);

// The whole names a name is made of, each ending with the "/" that joins it
// to the next; joined, they give the name back. Names joined by a "/" with a
// whole name on each side (OPENAI_API_KEY/ANTHROPIC_API_KEY) come apart
// there, and a line may break after that "/". A path or a URL
// (~/.omega/config.json, tools/forecast.mjs, https://host/v1/forecast) is
// one name, never broken.
export function namePieces(name) {
  if (name.includes("://") || !OWN_START.test(name)) return [name];
  const parts = name.split("/");
  const pieces = [parts[0]];
  for (let index = 1; index < parts.length; index += 1) {
    if (wholeName(parts[index - 1]) && wholeName(parts[index])) {
      pieces[pieces.length - 1] += "/";
      pieces.push(parts[index]);
    } else {
      pieces[pieces.length - 1] += `/${parts[index]}`;
    }
  }
  return pieces;
}
