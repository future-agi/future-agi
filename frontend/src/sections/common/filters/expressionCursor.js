// W5-1 expression language · the parser's token cursor (W5-SCOPE §5 A0).
// One cursor per parse: position, nesting depth and the shared error helpers
// the grammar (expressionParser.js) and the atoms (expressionAtoms.js) use.
import { EXPRESSION_LIMITS, TOO_DEEP, keywordOf } from "./expressionLexer";

export const CMP_OPS = Object.freeze(["=", "!=", ">", ">=", "<", "<="]);

export class ParseError extends Error {
  constructor(message, at, length) {
    super(message);
    this.at = at;
    this.length = Math.max(1, length ?? 1);
  }
}

/** A token as the user typed it, for error copy. */
export const show = (tok) =>
  tok.type === "str"
    ? JSON.stringify(tok.value)
    : tok.type === "name"
      ? `\`${tok.value}\``
      : String(tok.value);

/** The source span from node / token `a` to node / token `b`. */
export const span = (a, b) => ({ at: a.at, length: b.at + b.length - a.at });

/**
 * A cursor over `toks` (the lexer's tokens, ending with an `end` token).
 * @param {Array<{type: string, value: any, at: number, length: number}>} toks
 */
export function makeCursor(toks) {
  let pos = 0;
  let depth = 0;
  const peek = (k = 0) => toks[Math.min(pos + k, toks.length - 1)];
  const next = () => toks[pos++];
  const isOp = (tok, v) => tok.type === "op" && tok.value === v;
  const isKw = (tok, kw) => keywordOf(tok) === kw;
  const atEnd = () => peek().type === "end";

  const enter = (tok) => {
    depth += 1;
    if (depth > EXPRESSION_LIMITS.maxDepth)
      throw new ParseError(TOO_DEEP, tok.at, tok.length);
  };
  const leave = () => {
    depth -= 1;
  };

  // An operand was needed: name the dangling word at the end, or what we found.
  const needValue = (after) => {
    const tok = peek();
    if (tok.type === "end")
      throw new ParseError(
        `Unexpected “${show(after)}” at the end.`,
        after.at,
        after.length,
      );
    throw new ParseError(
      `Expected a value, found “${show(tok)}”.`,
      tok.at,
      tok.length,
    );
  };

  const expectClose = (open) => {
    if (isOp(peek(), ")")) return next();
    if (atEnd()) throw new ParseError("Missing “)”.", open.at, open.length);
    const tok = peek();
    throw new ParseError(
      `Expected “)” or “,”, found “${show(tok)}”.`,
      tok.at,
      tok.length,
    );
  };

  return {
    peek,
    next,
    isOp,
    isKw,
    atEnd,
    enter,
    leave,
    needValue,
    expectClose,
    get pos() {
      return pos;
    },
  };
}
