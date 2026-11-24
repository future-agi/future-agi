import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import { isBasicHtmlTag } from "src/sections/simulate/environments/buildEnvironment/console/basicHtml";

// The same parser the step panel renders with, so a label never drops a character the panel shows.
const parser = unified().use(remarkParse).use(remarkGfm);

// Blocks inside these sit on lines of their own.
const STACKED = new Set([
  "root",
  "blockquote",
  "list",
  "listItem",
  "table",
  "footnoteDefinition",
]);

const wordsOf = (node) => {
  // A tag the panel renders (<b>) shows no words of its own; any other html shows as text.
  if (node.type === "html" && isBasicHtmlTag(node.value))
    return /^<br/i.test(node.value.trim()) ? "\n" : "";
  // text, inlineCode and code.
  if (typeof node.value === "string") return node.value;
  if (node.type === "image" || node.type === "imageReference")
    return node.alt || "";
  if (node.type === "break") return "\n";
  const kids = node.children || [];
  if (node.type === "tableRow") return kids.map(wordsOf).join(" | ");
  if (node.type === "list") {
    const first = node.start ?? 1;
    return kids
      .map((item, i) => (node.ordered ? `${first + i}. ` : "") + wordsOf(item))
      .join("\n");
  }
  if (node.type === "listItem") {
    const box =
      node.checked === true ? "[x] " : node.checked === false ? "[ ] " : "";
    return box + kids.map(wordsOf).filter(Boolean).join("\n");
  }
  const parts = kids.map(wordsOf);
  return STACKED.has(node.type)
    ? parts.filter(Boolean).join("\n")
    : parts.join("");
};

// A markdown message as the words it shows once rendered, one block per line.
export const shownWords = (text) => wordsOf(parser.parse(String(text ?? "")));
