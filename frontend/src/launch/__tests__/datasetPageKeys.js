// L6 launch switch, test helper: every URL query key the dataset page reads,
// derived from the source (never a hand list). Plain node (fs, path), so the
// walk audit can run it too. A read the scanner cannot resolve to a literal
// key is reported in `dynamic` so the test fails closed on it.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

/** The dataset page's source: the page, its Row view, sheets and reads. */
export const DATASET_PAGE_DIRS = Object.freeze([
  "sections/develop-detail",
  "sections/develop/AddRowDrawer",
  "sections/common/Collab",
  "sections/common/RowPanel",
  "sections/common/AddData",
  "sections/common/RunSources",
  "api/develop",
]);

const SKIP = /(__tests__|__fixtures__|\.test\.|\.spec\.|\.stories\.)/;

function filesUnder(dir) {
  let out = [];
  let names = [];
  try {
    names = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of names) {
    const p = join(dir, name);
    if (SKIP.test(p)) continue;
    const st = statSync(p);
    if (st.isDirectory()) out = out.concat(filesUnder(p));
    else if (/\.(js|jsx|ts|tsx)$/.test(name)) out.push(p);
  }
  return out;
}

// A URLSearchParams-like receiver (`searchParams`, `rowParams`, `params`,
// `sp`, `usp`, `atMount`, `search`).
const RECEIVER =
  "(?:searchParams|[A-Za-z_$]*[pP]arams|sp|usp|atMount|search|qs|query)";
const LITERAL_READ = new RegExp(
  `\\b${RECEIVER}\\.(?:get|has|getAll)\\(\\s*["'\`]([\\w-]+)["'\`]\\s*\\)`,
  "g",
);
const CONST_READ = new RegExp(
  `(?:\\b${RECEIVER}|URLSearchParams\\([^)]*\\))\\.(?:get|has|getAll)\\(\\s*([A-Z][A-Z0-9_]*)\\s*\\)`,
  "g",
);
const NEW_USP_LITERAL =
  /URLSearchParams\([^)]*\)\s*\.(?:get|has|getAll)\(\s*["'`]([\w-]+)["'`]\s*\)/g;
const DYNAMIC_READ = new RegExp(
  `\\b${RECEIVER}\\.(?:get|has|getAll)\\(\\s*([a-z_$][\\w$]*)\\s*\\)`,
  "g",
);
const URL_STATE = /useUrlState\(\s*["'`]([\w-]+)["'`]/g;
const VIEW_URL_STATE = /useViewUrlState\(\s*([A-Za-z_$][\w$]*)\s*[,)]/g;
const STRING_CONST = /\b([A-Z][A-Z0-9_]*)\s*=\s*["'`]([\w-]+)["'`]/g;

const objectKeysOf = (text, name) => {
  const m = new RegExp(
    `\\b${name}\\s*=\\s*(?:Object\\.freeze\\()?\\{([\\s\\S]*?)\\}`,
  ).exec(text);
  if (!m) return null;
  return [...m[1].matchAll(/["']?([A-Za-z_][\w-]*)["']?\s*:/g)].map(
    (x) => x[1],
  );
};

/**
 * @param {string} srcRoot  absolute path of frontend/src
 * @returns {{reads: Array<{key: string, file: string, how: string}>,
 *   dynamic: Array<{file: string, expr: string}>}}
 */
export function scanDatasetPageKeys(srcRoot) {
  const files = DATASET_PAGE_DIRS.flatMap((d) => filesUnder(join(srcRoot, d)));
  const texts = new Map(files.map((f) => [f, readFileSync(f, "utf8")]));
  // String constants anywhere under src the reads may name.
  const consts = new Map();
  for (const f of filesUnder(srcRoot)) {
    const t = texts.get(f) ?? readFileSync(f, "utf8");
    for (const m of t.matchAll(STRING_CONST))
      if (!consts.has(m[1])) consts.set(m[1], m[2]);
  }
  const reads = [];
  const dynamic = [];
  for (const [f, t] of texts) {
    const file = relative(srcRoot, f);
    const add = (key, how) => reads.push({ key, file, how });
    for (const m of t.matchAll(LITERAL_READ)) add(m[1], "get");
    for (const m of t.matchAll(NEW_USP_LITERAL)) add(m[1], "get");
    for (const m of t.matchAll(URL_STATE)) add(m[1], "useUrlState");
    for (const m of t.matchAll(CONST_READ)) {
      const v = consts.get(m[1]);
      if (v) add(v, `get(${m[1]})`);
      else dynamic.push({ file, expr: m[0] });
    }
    for (const m of t.matchAll(VIEW_URL_STATE)) {
      const keys = objectKeysOf(t, m[1]);
      if (keys) keys.forEach((k) => add(k, `useViewUrlState(${m[1]})`));
      else dynamic.push({ file, expr: m[0] });
    }
    for (const m of t.matchAll(DYNAMIC_READ))
      dynamic.push({ file, expr: m[0] });
  }
  return { reads, dynamic };
}
