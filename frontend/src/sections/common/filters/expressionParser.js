// W5-1 expression language · the parser (W5-SCOPE §5 A0). Tokens → a tree.
// Recursive descent over the fixed grammar below; no regex, no eval.
//
//   expr   := or
//   or     := and ("or" and)*
//   and    := not ("and" not)*
//   not    := "not" not | cmp
//   cmp    := sum [ ("=" | "!=" | ">" | ">=" | "<" | "<=") sum
//                 | ["not"] "in" ( "(" literal ("," literal)* ")" | sum )
//                 | ["not"] "contains" sum | ["not"] "startswith" sum
//                 | "is" ["not"] "empty" ]
//   sum    := term (("+" | "-") term)*
//   term   := unary (("*" | "/") unary)*
//   unary  := "-" unary | atom
//   atom   := number | string | true | false | null | verdict-word
//           | field | func "(" [expr ("," expr)*] ")" | "(" expr ")"
//   field  := [runref "."] name ("." name)*
//   runref := #2B | #2B·2 | baseline | any | all | run("<label or id>")
//
// Nodes (every node has `at` and `length`, the source span):
//   {t:"or"|"and", items[]} · {t:"not", e} · {t:"cmp", op, l, r}
//   {t:"in", neg, l, items[] | list} · {t:"contains"|"startswith", neg, l, r}
//   {t:"empty", neg, l} · {t:"arith", op, l, r} · {t:"neg", e}
//   {t:"lit", v} · {t:"word", v} (a verdict word) · {t:"call", fn, args[]}
//   {t:"field", run: null | {kind:"label", label} | {kind:"baseline"|"any"|"all"}
//      | {kind:"run", ref}, path: string[], runAt, runLength}
import { keywordOf, tokenize } from "./expressionLexer";
import {
  CMP_OPS,
  ParseError,
  makeCursor,
  show,
  span,
} from "./expressionCursor";
import { FUNCTIONS, parseAtom, parseLiteral } from "./expressionAtoms";

export { FUNCTIONS };

/**
 * Parse one expression.
 * @param {string} text
 * @returns {{ast: object} | {error: {message: string, at: number, length: number}}}
 */
export function parseExpression(text) {
  const lexed = tokenize(text);
  if (lexed.error) return lexed;
  const c = makeCursor(lexed.tokens);
  const { peek, next, isOp, isKw, atEnd, enter, leave, needValue } = c;

  function parseOr() {
    const first = parseAnd();
    const items = [first];
    while (isKw(peek(), "or")) {
      const kw = next();
      if (atEnd()) needValue(kw);
      items.push(parseAnd());
    }
    return items.length === 1
      ? first
      : { t: "or", items, ...span(items[0], items[items.length - 1]) };
  }

  function parseAnd() {
    const first = parseNot();
    const items = [first];
    while (isKw(peek(), "and")) {
      const kw = next();
      if (atEnd()) needValue(kw);
      items.push(parseNot());
    }
    return items.length === 1
      ? first
      : { t: "and", items, ...span(items[0], items[items.length - 1]) };
  }

  function parseNot() {
    if (isKw(peek(), "not")) {
      const kw = next();
      if (atEnd()) needValue(kw);
      enter(kw);
      const e = parseNot();
      leave();
      return { t: "not", e, ...span(kw, e) };
    }
    return parseCmp();
  }

  // `in` with a literal list `( ... )` or any value (a list field).
  function parseIn(l, neg) {
    const inTok = next();
    if (isOp(peek(), "(")) {
      const open = next();
      if (isOp(peek(), ")") || atEnd())
        throw new ParseError(
          "Expected a value after “in”.",
          inTok.at,
          inTok.length,
        );
      const items = [parseLiteral(c, inTok)];
      while (isOp(peek(), ",")) {
        next();
        items.push(parseLiteral(c, inTok));
      }
      const close = c.expectClose(open);
      return { t: "in", neg, l, items, ...span(l, close) };
    }
    if (atEnd())
      throw new ParseError(
        "Expected a value after “in”.",
        inTok.at,
        inTok.length,
      );
    const list = parseSum();
    return { t: "in", neg, l, list, ...span(l, list) };
  }

  // `is [not] empty`
  function parseIsEmpty(l) {
    const isTok = next();
    let isNeg = false;
    if (isKw(peek(), "not")) {
      next();
      isNeg = true;
    }
    if (!isKw(peek(), "empty")) {
      const bad = peek();
      throw new ParseError(
        bad.type === "end"
          ? `Unexpected “is${isNeg ? " not" : ""}” at the end.`
          : "Write “is empty” or “is not empty”.",
        isTok.at,
        bad.type === "end" ? isTok.length : bad.at + bad.length - isTok.at,
      );
    }
    const end = next();
    return { t: "empty", neg: isNeg, l, ...span(l, end) };
  }

  function parseCmp() {
    const l = parseSum();
    const tok = peek();
    if (tok.type === "op" && CMP_OPS.includes(tok.value)) {
      next();
      if (atEnd()) needValue(tok);
      const r = parseSum();
      return {
        t: "cmp",
        op: tok.value,
        l,
        r,
        opAt: tok.at,
        opLength: tok.length,
        ...span(l, r),
      };
    }
    let neg = false;
    let negTok = null;
    if (
      isKw(tok, "not") &&
      ["in", "contains", "startswith"].includes(keywordOf(peek(1)))
    ) {
      negTok = next();
      neg = true;
    }
    const kw = keywordOf(peek());
    if (kw === "in") return parseIn(l, neg);
    if (kw === "contains" || kw === "startswith") {
      const kwTok = next();
      if (atEnd()) needValue(kwTok);
      const r = parseSum();
      return { t: kw, neg, l, r, ...span(l, r) };
    }
    if (negTok)
      throw new ParseError(
        "Expected in, contains or startswith after “not”.",
        negTok.at,
        negTok.length,
      );
    if (kw === "is") return parseIsEmpty(l);
    return l;
  }

  function parseSum() {
    let l = parseTerm();
    while (isOp(peek(), "+") || isOp(peek(), "-")) {
      const op = next();
      if (atEnd()) needValue(op);
      const r = parseTerm();
      l = { t: "arith", op: op.value, l, r, ...span(l, r) };
    }
    return l;
  }

  function parseTerm() {
    let l = parseUnary();
    while (isOp(peek(), "*") || isOp(peek(), "/")) {
      const op = next();
      if (atEnd()) needValue(op);
      const r = parseUnary();
      l = { t: "arith", op: op.value, l, r, ...span(l, r) };
    }
    return l;
  }

  function parseUnary() {
    if (isOp(peek(), "-")) {
      const op = next();
      if (atEnd()) needValue(op);
      enter(op);
      const e = parseUnary();
      leave();
      if (e.t === "lit" && typeof e.v === "number")
        return { t: "lit", v: -e.v, ...span(op, e) };
      return { t: "neg", e, ...span(op, e) };
    }
    return parseAtom(c, parseOr);
  }

  try {
    if (atEnd())
      return { error: { message: "Type an expression.", at: 0, length: 1 } };
    const ast = parseOr();
    if (!atEnd()) {
      const tok = peek();
      if (isOp(tok, ")"))
        throw new ParseError("Unexpected “)”.", tok.at, tok.length);
      throw new ParseError(
        `Unexpected “${show(tok)}”. Join conditions with and / or.`,
        tok.at,
        tok.length,
      );
    }
    return { ast };
  } catch (e) {
    if (e instanceof ParseError)
      return { error: { message: e.message, at: e.at, length: e.length } };
    throw e;
  }
}
