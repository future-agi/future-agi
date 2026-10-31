// Editing assistance, not a SQL validator. The server compiler owns validity,
// access and scoping. Never infer fields that are absent from the wire catalog.
export const quoteIdentifier = (value, delimiter = "`") =>
  `${delimiter}${value.replace(/\\/g, "\\\\").replaceAll(delimiter, `\\${delimiter}`)}${delimiter}`;

const KEYWORDS = [
  "SELECT",
  "FROM",
  "WHERE",
  "GROUP BY",
  "ORDER BY",
  "HAVING",
  "LIMIT",
  "LEFT JOIN",
  "INNER JOIN",
  "ON",
  "AS",
  "AND",
  "OR",
  "NOT",
  "IS NULL",
  "IS NOT NULL",
  "IN",
  "DISTINCT",
  "ASC",
  "DESC",
];
const RESERVED = new Set(
  `${KEYWORDS.join(" ")} JOIN RIGHT FULL CROSS OUTER ANY ALL UNION OFFSET WITH USING PREWHERE FINAL SETTINGS FORMAT BY`.split(
    " ",
  ),
);
const FUNCTIONS = [
  "count",
  "countIf",
  "sum",
  "avg",
  "min",
  "max",
  "uniqExact",
  "toDate",
  "toStartOfHour",
  "toString",
  "coalesce",
];
const isKeyword = (token, word) =>
  token?.kind === "word" && token.value.toUpperCase() === word;
const isSymbol = (token, value) =>
  token?.kind === "symbol" && token.value === value;
const isIdentifier = (token) =>
  token &&
  (token.kind === "identifier" ||
    (token.kind === "word" && !RESERVED.has(token.value.toUpperCase())));

// Token offsets remain in the original text (including newlines). Comments and
// string literals are opaque, so their contents cannot introduce relations.
function tokenize(sql) {
  const tokens = [];
  let i = 0;
  let depth = 0;
  while (i < sql.length) {
    if (/\s/.test(sql[i])) {
      i += 1;
      continue;
    }
    const start = i;
    let kind = "symbol";
    let value = "";
    let closed = true;
    const heredoc =
      sql[i] === "$" ? sql.slice(i).match(/^\$[a-zA-Z_0-9]*\$/)?.[0] : null;
    if (heredoc) {
      kind = "string";
      const end = sql.indexOf(heredoc, i + heredoc.length);
      closed = end !== -1;
      i = closed ? end + heredoc.length : sql.length;
    } else if (sql.startsWith("/*", i)) {
      kind = "comment";
      let nesting = 1;
      i += 2;
      while (i < sql.length && nesting) {
        if (sql.startsWith("/*", i)) {
          nesting += 1;
          i += 2;
        } else if (sql.startsWith("*/", i)) {
          nesting -= 1;
          i += 2;
        } else i += 1;
      }
      closed = nesting === 0;
    } else if (sql.startsWith("--", i) || sql[i] === "#") {
      kind = "comment";
      const end = sql.indexOf("\n", i + (sql[i] === "#" ? 1 : 2));
      closed = end !== -1;
      i = closed ? end : sql.length;
    } else if (["'", "`", '"'].includes(sql[i])) {
      const quote = sql[i++];
      kind = quote === "'" ? "string" : "identifier";
      closed = false;
      while (i < sql.length) {
        if (sql[i] === "\\" && i + 1 < sql.length) {
          value += sql[i + 1];
          i += 2;
        } else if (sql[i] === quote) {
          if (sql[i + 1] === quote) {
            value += quote;
            i += 2;
          } else {
            i += 1;
            closed = true;
            break;
          }
        } else {
          value += sql[i++];
        }
      }
    } else if (/[a-zA-Z_]/.test(sql[i])) {
      kind = "word";
      while (i < sql.length && /[a-zA-Z_0-9$]/.test(sql[i])) value += sql[i++];
    } else {
      value = sql[i++];
      if (value === ")") depth = Math.max(0, depth - 1);
    }
    tokens.push({ kind, value, start, end: i, depth, closed });
    if (value === "(" && kind === "symbol") depth += 1;
  }
  return tokens;
}

function queryScope(allTokens, offset) {
  const startOfStatement =
    allTokens
      .filter((token) => isSymbol(token, ";") && token.end <= offset)
      .at(-1)?.end ?? 0;
  const endOfStatement =
    allTokens.find((token) => isSymbol(token, ";") && token.start >= offset)
      ?.start ?? Infinity;
  const tokens = allTokens.filter(
    (token) => token.start >= startOfStatement && token.end <= endOfStatement,
  );
  let scope = tokens;
  for (let i = 0; i < tokens.length; i += 1) {
    const start = tokens[i];
    if (!isKeyword(start, "SELECT") || start.start > offset) continue;
    let end = i + 1;
    while (end < tokens.length) {
      const token = tokens[end];
      if (
        token.depth < start.depth ||
        isSymbol(token, ";") ||
        (token.depth === start.depth && isKeyword(token, "UNION"))
      )
        break;
      end += 1;
    }
    if (end === tokens.length || offset <= tokens[end].start) {
      scope = tokens.slice(i, end);
    }
  }
  return scope;
}

function relationsIn(tokens, datasets) {
  const relations = [];
  let from = false;
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (
      ["WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "ON", "USING"].some(
        (word) => isKeyword(token, word),
      )
    )
      from = false;
    if (
      isKeyword(token, "FROM") ||
      isKeyword(token, "JOIN") ||
      (from && isSymbol(token, ","))
    ) {
      from = true;
      const name = tokens[i + 1];
      if (!isIdentifier(name)) continue;
      // A qualified/physical table is not a catalog relation.
      if (isSymbol(tokens[i + 2], ".")) continue;
      const dataset =
        !isSymbol(tokens[i + 2], "(") &&
        datasets.find((entry) => entry.name === name.value);
      let alias = tokens[i + 2];
      if (isKeyword(alias, "AS")) alias = tokens[i + 3];
      relations.push({
        dataset,
        qualifier: isIdentifier(alias) ? alias.value : name.value,
      });
    }
  }
  return relations;
}

export function getSQLCompletions(sql, offset, datasets = []) {
  const cursor = Math.max(0, Math.min(offset, sql.length));
  const tokens = tokenize(sql);
  const opaque = tokens.find(
    (token) =>
      ["comment", "string"].includes(token.kind) &&
      cursor > token.start &&
      (cursor < token.end ||
        ((!token.closed ||
          sql.startsWith("--", token.start) ||
          sql[token.start] === "#") &&
          cursor === token.end)),
  );
  if (opaque) return { from: cursor, to: cursor, suggestions: [] };
  const live = tokens.filter(
    (token) => !["comment", "string"].includes(token.kind),
  );
  const active = live.find(
    (token) =>
      ["word", "identifier"].includes(token.kind) &&
      cursor > token.start &&
      cursor <= token.end,
  );
  const from = active?.start ?? cursor;
  const to = active?.end ?? cursor;
  const typed = sql.slice(from, cursor);
  const quoted = active?.kind === "identifier";
  // Decode the logical prefix for catalog matching, including doubled quotes
  // and backslash escapes, but keep its original spelling for Monaco scoring.
  const prefix = quoted ? tokenize(typed)[0].value : typed;
  const filterPrefix =
    quoted && active.closed && cursor === active.end
      ? typed.slice(0, -1)
      : typed;
  const scope = queryScope(live, cursor);
  const before = scope.filter((token) => token.end <= from);
  const last = before.at(-1);
  const baseDepth = scope[0]?.depth ?? 0;
  const shallow = scope.filter((token) => token.depth === baseDepth);
  // A CTE may shadow a catalog name. Its projected schema is not authoritative
  // catalog data, so be conservative rather than suggesting the base fields.
  const statementStart =
    live.filter((token) => isSymbol(token, ";") && token.end <= cursor).at(-1)
      ?.end ?? 0;
  const shadowed = new Set(
    live
      .filter(
        (token, index) =>
          token.start >= statementStart &&
          token.start < (scope[0]?.start ?? 0) &&
          token.depth <= baseDepth &&
          isIdentifier(token) &&
          isKeyword(live[index + 1], "AS") &&
          isSymbol(live[index + 2], "("),
      )
      .map((token) => token.value),
  );
  const relations = relationsIn(
    shallow,
    datasets.filter((dataset) => !shadowed.has(dataset.name)),
  );
  const available = datasets.filter((dataset) => dataset.available !== false);
  const lastClause = before
    .filter(
      (token) =>
        token.depth === baseDepth &&
        [
          "SELECT",
          "FROM",
          "JOIN",
          "ON",
          "WHERE",
          "GROUP",
          "ORDER",
          "HAVING",
          "LIMIT",
        ].some((word) => isKeyword(token, word)),
    )
    .at(-1);
  const expectsDataset =
    isKeyword(last, "FROM") ||
    isKeyword(last, "JOIN") ||
    (isSymbol(last, ",") && isKeyword(lastClause, "FROM"));
  let suggestions;
  if (expectsDataset) {
    suggestions = available.map((dataset) => ({
      label: dataset.name,
      insertText: quoteIdentifier(dataset.name),
      kind: "Class",
      detail: "Dataset",
      documentation: dataset.description,
    }));
  } else {
    const qualified = isSymbol(last, ".");
    const qualifier = qualified ? before.at(-2)?.value : null;
    const inScope = relations.filter(
      (relation) =>
        relation.dataset?.available !== false &&
        relation.dataset &&
        (!qualified || relation.qualifier === qualifier),
    );
    suggestions = inScope.flatMap(({ dataset, qualifier: relationName }) =>
      dataset.columns.map((column) => ({
        label: column.name,
        insertText: `${!qualified && relations.length > 1 ? `${quoteIdentifier(relationName)}.` : ""}${quoteIdentifier(column.name)}`,
        kind: "Field",
        detail: `${relationName} · ${column.type}`,
        documentation: `${dataset.name}.${column.name} · ${column.type}`,
      })),
    );
    if (!qualified) {
      suggestions.push(
        ...KEYWORDS.map((keyword) => ({
          label: keyword,
          insertText: keyword,
          kind: "Keyword",
          detail: "SQL keyword",
        })),
        ...FUNCTIONS.map((name) => ({
          label: name,
          insertText: `${name}()`,
          kind: "Function",
          detail: "SQL function",
        })),
      );
    }
  }
  return {
    from,
    to,
    suggestions: suggestions
      .filter((item) =>
        item.label.toLowerCase().startsWith(prefix.toLowerCase()),
      )
      .map((item, index) => ({
        ...item,
        // The full-token range also replaces an existing suffix/closing quote.
        // Monaco scores everything from that range's start to the caret, so an
        // unquoted label alone would hide quoted suggestions. Preserve the
        // user's delimiter/escape spelling, then append the remaining name.
        filterText: quoted
          ? `${filterPrefix}${quoteIdentifier(item.label.slice(prefix.length), sql[from]).slice(1)}`
          : item.label,
        sortText: String(index).padStart(4, "0"),
      })),
  };
}
