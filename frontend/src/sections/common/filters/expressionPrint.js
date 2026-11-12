// W5-1 · the printer (W5-SCOPE §5 A0): a tree → canonical one-line text.
// Keywords lowercase, strings in double quotes, names back-quoted only when
// they need it, parentheses only where precedence needs them. Printing a
// parse of the printed text gives the same text. Pure.
import { quoteSegment } from "./fieldCatalog";

const PREC = {
  or: 1,
  and: 2,
  not: 3,
  cmp: 4,
  in: 4,
  contains: 4,
  startswith: 4,
  empty: 4,
  sum: 5,
  term: 6,
  neg: 7,
  atom: 8,
};

const precOf = (node) => {
  switch (node.t) {
    case "or":
    case "and":
    case "not":
      return PREC[node.t];
    case "cmp":
    case "in":
    case "contains":
    case "startswith":
    case "empty":
      return PREC.cmp;
    case "arith":
      return node.op === "+" || node.op === "-" ? PREC.sum : PREC.term;
    case "neg":
      return PREC.neg;
    default:
      return PREC.atom;
  }
};

const lit = (v) => {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (typeof v === "number") return String(v);
  return JSON.stringify(String(v));
};

const runText = (run) => {
  if (!run) return "";
  if (run.kind === "label") return `${run.label}.`;
  if (run.kind === "run") return `run(${JSON.stringify(run.ref)}).`;
  return `${run.kind}.`;
};

/** Canonical text of a tree. */
export function printExpression(node) {
  if (!node) return "";
  const wrap = (child, min) => {
    const text = printExpression(child);
    return precOf(child) < min ? `(${text})` : text;
  };
  switch (node.t) {
    case "or":
    case "and":
      return node.items
        .map((x) => wrap(x, PREC[node.t] + 1))
        .join(` ${node.t} `);
    case "not":
      return `not ${wrap(node.e, PREC.not)}`;
    case "cmp":
      return `${wrap(node.l, PREC.sum)} ${node.op} ${wrap(node.r, PREC.sum)}`;
    case "in":
      return `${wrap(node.l, PREC.sum)} ${node.neg ? "not in" : "in"} ${node.items ? `(${node.items.map(printExpression).join(", ")})` : wrap(node.list, PREC.sum)}`;
    case "contains":
    case "startswith":
      return `${wrap(node.l, PREC.sum)} ${node.neg ? "not " : ""}${node.t} ${wrap(node.r, PREC.sum)}`;
    case "empty":
      return `${wrap(node.l, PREC.sum)} is ${node.neg ? "not " : ""}empty`;
    case "arith": {
      const p = precOf(node);
      // Left-associative: the right side needs parentheses at equal precedence.
      return `${wrap(node.l, p)} ${node.op} ${wrap(node.r, p + 1)}`;
    }
    case "neg":
      return `-${wrap(node.e, PREC.neg)}`;
    case "lit":
      return lit(node.v);
    case "word":
      return node.v;
    case "call":
      return `${node.fn}(${node.args.map(printExpression).join(", ")})`;
    case "field":
      return `${runText(node.run)}${node.path.map(quoteSegment).join(".")}`;
    default:
      return "";
  }
}
