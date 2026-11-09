// W5-1 expression language · the parser's atoms (W5-SCOPE §5 A0): literals,
// list items, fields with their run reference, function calls and
// parentheses. The grammar above them is expressionParser.js; both share one
// cursor (expressionCursor.js). `parseOr` is passed in for nested expressions.
//
//   atom   := number | string | true | false | null | verdict-word
//           | field | func "(" [expr ("," expr)*] ")" | "(" expr ")"
//   field  := [runref "."] name ("." name)*
//   runref := #2B | #2B·2 | baseline | any | all | run("<label or id>")
import { RUN_WORDS, VERDICT_WORDS, keywordOf } from "./expressionLexer";
import { ParseError, show, span } from "./expressionCursor";

export const FUNCTIONS = Object.freeze({
  len: { min: 1, max: 1 },
  lower: { min: 1, max: 1 },
  upper: { min: 1, max: 1 },
  abs: { min: 1, max: 1 },
  round: { min: 1, max: 2 },
  min: { min: 1, max: Infinity },
  max: { min: 1, max: Infinity },
  coalesce: { min: 1, max: Infinity },
});

/** One item of an `in (...)` list: a literal or a bare word. */
export function parseLiteral(c, inTok) {
  const tok = c.peek();
  if (tok.type === "end")
    throw new ParseError(
      "Expected a value after “in”.",
      inTok.at,
      inTok.length,
    );
  if (c.isOp(tok, "-") && c.peek(1).type === "num") {
    c.next();
    const n = c.next();
    return { t: "lit", v: -n.value, ...span(tok, n) };
  }
  if (tok.type === "num" || tok.type === "str") {
    c.next();
    return { t: "lit", v: tok.value, at: tok.at, length: tok.length };
  }
  const kw = keywordOf(tok);
  if (kw === "true" || kw === "false" || kw === "null") {
    c.next();
    return {
      t: "lit",
      v: kw === "null" ? null : kw === "true",
      at: tok.at,
      length: tok.length,
    };
  }
  // Bare words: verdict words, or a field's known values (validated).
  if (tok.type === "ident" && !keywordOf(tok)) {
    c.next();
    return {
      t: "word",
      v: tok.value.toLowerCase(),
      at: tok.at,
      length: tok.length,
    };
  }
  throw new ParseError(
    `A list holds values ("es", 3, true), not “${show(tok)}”.`,
    tok.at,
    tok.length,
  );
}

function parseSegment(c, after) {
  const tok = c.peek();
  if (tok.type === "ident" || tok.type === "name") {
    c.next();
    return tok;
  }
  if (tok.type === "num" && Number.isInteger(tok.value)) {
    c.next();
    return { ...tok, value: String(tok.value) };
  }
  if (tok.type === "end")
    throw new ParseError(
      `Expected a field name after “${show(after)}.”.`,
      after.at,
      after.length + 1,
    );
  throw new ParseError(
    `Expected a field name after “.”, found “${show(tok)}”.`,
    tok.at,
    tok.length,
  );
}

function parsePath(c, first, run, runTok) {
  const path = [first.value];
  let last = first;
  while (c.isOp(c.peek(), ".")) {
    const dot = c.next();
    last = parseSegment(c, { ...last, length: dot.at - last.at });
    path.push(last.value);
  }
  const head = runTok ?? first;
  return {
    t: "field",
    run,
    path,
    runAt: runTok?.at ?? null,
    runLength: runTok?.length ?? null,
    ...span(head, last),
  };
}

// `<run>.` then a field.
function runField(c, run, runTok) {
  if (!c.isOp(c.peek(), "."))
    throw new ParseError(
      `A run needs a field after it, like ${runTok.value}.output.`,
      runTok.at,
      runTok.length,
    );
  c.next();
  const first = parseSegment(c, runTok);
  return parsePath(c, first, run, runTok);
}

// run("<label or id>").field
function parseRunCall(c, tok) {
  c.next();
  const open = c.next();
  const ref = c.peek();
  if (ref.type !== "str")
    throw new ParseError(
      'Write run("<label or id>").',
      tok.at,
      ref.at + ref.length - tok.at,
    );
  c.next();
  const close = c.expectClose(open);
  const runTok = {
    type: "run",
    value: `run(${JSON.stringify(ref.value)})`,
    at: tok.at,
    length: close.at + close.length - tok.at,
  };
  return runField(c, { kind: "run", ref: ref.value }, runTok);
}

// fn(arg, ...)
function parseCall(c, tok, lower, parseOr) {
  c.next();
  const open = c.next();
  c.enter(open);
  const args = [];
  if (!c.isOp(c.peek(), ")")) {
    if (c.atEnd()) throw new ParseError("Missing “)”.", open.at, open.length);
    args.push(parseOr());
    while (c.isOp(c.peek(), ",")) {
      const comma = c.next();
      if (c.atEnd()) c.needValue(comma);
      args.push(parseOr());
    }
  }
  c.leave();
  const close = c.expectClose(open);
  return {
    t: "call",
    fn: lower,
    args,
    nameAt: tok.at,
    nameLength: tok.length,
    at: tok.at,
    length: close.at + close.length - tok.at,
  };
}

function parseIdent(c, tok, parseOr) {
  const lower = tok.value.toLowerCase();
  const kw = keywordOf(tok);
  if (kw === "true" || kw === "false") {
    c.next();
    return { t: "lit", v: kw === "true", at: tok.at, length: tok.length };
  }
  if (kw === "null") {
    c.next();
    return { t: "lit", v: null, at: tok.at, length: tok.length };
  }
  if (kw)
    throw new ParseError(
      c.peek(1).type === "end" && c.pos > 0
        ? `Unexpected “${tok.value}” at the end.`
        : `Expected a value, found “${tok.value}”.`,
      tok.at,
      tok.length,
    );
  const nextTok = c.peek(1);
  if (lower === "run" && c.isOp(nextTok, "(")) return parseRunCall(c, tok);
  if (
    Object.prototype.hasOwnProperty.call(FUNCTIONS, lower) &&
    c.isOp(nextTok, "(")
  )
    return parseCall(c, tok, lower, parseOr);
  if (c.isOp(nextTok, "(")) {
    throw new ParseError(
      `Unknown function “${tok.value}”. Functions: ${Object.keys(FUNCTIONS).join(", ")}.`,
      tok.at,
      tok.length,
    );
  }
  if (RUN_WORDS.includes(lower) && c.isOp(nextTok, ".")) {
    c.next();
    return runField(c, { kind: lower }, tok);
  }
  if (VERDICT_WORDS.includes(lower) && !c.isOp(nextTok, ".")) {
    c.next();
    return { t: "word", v: lower, at: tok.at, length: tok.length };
  }
  c.next();
  return parsePath(c, tok, null, null);
}

/** One atom at the cursor. */
export function parseAtom(c, parseOr) {
  const tok = c.peek();
  if (tok.type === "end")
    throw new ParseError("Type an expression.", tok.at, 1);
  if (tok.type === "num" || tok.type === "str") {
    c.next();
    return { t: "lit", v: tok.value, at: tok.at, length: tok.length };
  }
  if (c.isOp(tok, "(")) {
    const open = c.next();
    c.enter(open);
    if (c.atEnd()) throw new ParseError("Missing “)”.", open.at, open.length);
    const e = parseOr();
    c.leave();
    const close = c.expectClose(open);
    return {
      ...e,
      paren: true,
      at: open.at,
      length: close.at + close.length - open.at,
      innerAt: e.at,
      innerLength: e.length,
    };
  }
  if (tok.type === "runref") {
    c.next();
    return runField(c, { kind: "label", label: tok.value }, tok);
  }
  if (tok.type === "name") {
    c.next();
    return parsePath(c, tok, null, null);
  }
  if (tok.type === "ident") return parseIdent(c, tok, parseOr);
  if (tok.type === "op")
    throw new ParseError(
      `Expected a value, found “${tok.value}”.`,
      tok.at,
      tok.length,
    );
  throw new ParseError(`Unexpected “${show(tok)}”.`, tok.at, tok.length);
}
