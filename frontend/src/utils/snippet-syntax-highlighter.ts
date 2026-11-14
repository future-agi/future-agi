import SyntaxHighlighter, {
  type PrismGrammar,
  type Refractor,
} from "react-syntax-highlighter/dist/esm/prism-light";
import cssExtras from "react-syntax-highlighter/dist/esm/languages/prism/css-extras";
import graphql from "react-syntax-highlighter/dist/esm/languages/prism/graphql";
import javadoclike from "react-syntax-highlighter/dist/esm/languages/prism/javadoclike";
import jsExtras from "react-syntax-highlighter/dist/esm/languages/prism/js-extras";
import jsTemplates from "react-syntax-highlighter/dist/esm/languages/prism/js-templates";
import jsdoc from "react-syntax-highlighter/dist/esm/languages/prism/jsdoc";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";

// Extensions modify an existing grammar without adding their own registry entry.
// Record them so a later full Prism import does not apply the same extension twice.
function registerExtension(refractor: Refractor, grammar: PrismGrammar) {
  refractor.register(grammar);
  refractor.languages[grammar.displayName] ??= {};
}

function snippetTypescript(refractor: Refractor) {
  // Match the full build's JavaScript augmentations before TypeScript clones it.
  // CSS, GraphQL and SQL are used inside tagged templates; markup is in core.
  registerExtension(refractor, cssExtras);
  refractor.register(graphql);
  refractor.register(javadoclike);
  registerExtension(refractor, jsExtras);
  refractor.register(sql);
  registerExtension(refractor, jsTemplates);
  typescript(refractor);
  // Full Prism augments embedded JavaScript docs after TypeScript clones it,
  // leaving ordinary TypeScript docs on javadoclike rather than jsdoc.
  refractor.register(jsdoc);
}
snippetTypescript.displayName = "typescript";

SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("typescript", snippetTypescript);

export default SyntaxHighlighter;
