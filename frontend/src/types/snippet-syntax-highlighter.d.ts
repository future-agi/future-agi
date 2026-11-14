declare module "react-syntax-highlighter/dist/esm/prism-light" {
  import type { ComponentType, CSSProperties, HTMLAttributes } from "react";

  export type Refractor = {
    languages: Record<string, unknown>;
    register: (grammar: PrismGrammar) => void;
  };
  export type PrismGrammar = ((refractor: Refractor) => void) & {
    displayName: string;
    aliases?: string[];
  };

  const SyntaxHighlighter: ComponentType<{
    children?: string;
    language?: string;
    useInlineStyles?: boolean;
    customStyle?: CSSProperties;
    codeTagProps?: HTMLAttributes<HTMLElement>;
  }> & {
    registerLanguage: (name: string, grammar: PrismGrammar) => void;
  };
  export default SyntaxHighlighter;
}

declare module "react-syntax-highlighter/dist/esm/languages/prism/*" {
  import type { PrismGrammar } from "react-syntax-highlighter/dist/esm/prism-light";
  const grammar: PrismGrammar;
  export default grammar;
}
