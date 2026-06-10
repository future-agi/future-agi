import React, { useEffect, useRef } from "react";
import Editor from "@monaco-editor/react";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import {
  oneDark,
  oneLight,
} from "react-syntax-highlighter/dist/esm/styles/prism";

const PYTHON_SNIPPETS = [
  {
    label: "def evaluate",
    insertText:
      'def evaluate(output, expected, **kwargs):\n    """Evaluate the output against expected."""\n    return output == expected',
    detail: "Eval function template",
  },
  { label: "return True", insertText: "return True", detail: "Return pass" },
  { label: "return False", insertText: "return False", detail: "Return fail" },
  {
    label: "json.loads",
    insertText: "json.loads(${1:data})",
    detail: "Parse JSON string",
    insertTextRules: 4,
  },
  {
    label: "isinstance",
    insertText: "isinstance(${1:obj}, ${2:type})",
    detail: "Type check",
    insertTextRules: 4,
  },
  {
    label: "str.strip().lower()",
    insertText: "str(${1:value}).strip().lower()",
    detail: "Normalize string",
    insertTextRules: 4,
  },
  {
    label: "len()",
    insertText: "len(${1:obj})",
    detail: "Get length",
    insertTextRules: 4,
  },
  {
    label: "try/except",
    insertText: "try:\n    ${1:pass}\nexcept Exception as e:\n    return False",
    detail: "Try/except block",
    insertTextRules: 4,
  },
];

const JS_SNIPPETS = [
  {
    label: "function evaluate",
    insertText:
      "function evaluate(output, expected) {\n    // Return true for pass, false for fail\n    return output === expected;\n}",
    detail: "Eval function template",
  },
  {
    label: "JSON.parse",
    insertText: "JSON.parse(${1:data})",
    detail: "Parse JSON string",
    insertTextRules: 4,
  },
  {
    label: "try/catch",
    insertText: "try {\n    ${1}\n} catch (e) {\n    return false;\n}",
    detail: "Try/catch block",
    insertTextRules: 4,
  },
  {
    label: "typeof",
    insertText: "typeof ${1:value}",
    detail: "Type check",
    insertTextRules: 4,
  },
  {
    label: "trim().toLowerCase()",
    insertText: "String(${1:value}).trim().toLowerCase()",
    detail: "Normalize string",
    insertTextRules: 4,
  },
];

const CodeEditor = ({
  value,
  onChange,
  language = "python",
  height = "300px",
  disabled = false,
  placeholder = "",
  ariaLabel = "Code editor",
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const editorRef = useRef(null);
  const monacoRef = useRef(null);
  const completionProviderRef = useRef(null);
  const langConfigRef = useRef(null);

  const handleEditorDidMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;

    // Define custom dark theme that blends with MUI dark mode
    monaco.editor.defineTheme("futureagi-dark", {
      base: "vs-dark",
      inherit: true,
      rules: [],
      colors: {
        "editor.background": "#141A21",
        "editor.lineHighlightBackground": "#1a2230",
        "editorLineNumber.foreground": "#4a5568",
        "editorLineNumber.activeForeground": "#a0aec0",
        "editor.selectionBackground": "#2d3748",
        "editorCursor.foreground": "#7c3aed",
        "editorGutter.background": "#141A21",
      },
    });

    // Define custom light theme
    monaco.editor.defineTheme("futureagi-light", {
      base: "vs",
      inherit: true,
      rules: [],
      colors: {
        "editor.background": "#FAFBFC",
        "editor.lineHighlightBackground": "#f1f5f9",
        "editorLineNumber.foreground": "#94a3b8",
        "editorLineNumber.activeForeground": "#475569",
        "editorGutter.background": "#FAFBFC",
      },
    });

    // Apply the appropriate theme
    monaco.editor.setTheme(isDark ? "futureagi-dark" : "futureagi-light");

    // Auto-closing pairs
    setTimeout(() => {
      langConfigRef.current = monaco.languages.setLanguageConfiguration(
        language,
        {
          autoClosingPairs: [
            { open: "(", close: ")" },
            { open: "[", close: "]" },
            { open: "{", close: "}" },
            { open: "'", close: "'" },
            { open: '"', close: '"' },
            { open: "`", close: "`" },
          ],
        },
      );
    }, 0);

    // Completion provider with snippets
    const snippets = language === "python" ? PYTHON_SNIPPETS : JS_SNIPPETS;
    completionProviderRef.current =
      monaco.languages.registerCompletionItemProvider(language, {
        provideCompletionItems: (model, position) => {
          const word = model.getWordUntilPosition(position);
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: word.startColumn,
            endColumn: word.endColumn,
          };

          return {
            suggestions: snippets.map((s) => ({
              label: s.label,
              kind: monaco.languages.CompletionItemKind.Snippet,
              insertText: s.insertText,
              insertTextRules: s.insertTextRules
                ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet
                : undefined,
              detail: s.detail,
              range,
            })),
          };
        },
      });
  };

  useEffect(() => {
    return () => {
      if (completionProviderRef.current) {
        completionProviderRef.current.dispose();
      }
      if (langConfigRef.current) {
        langConfigRef.current.dispose();
      }
    };
  }, []);

  // Read-only view: plain syntax-highlighted code block (scrollable, not-allowed cursor)
  if (disabled) {
    const syntaxLang = language === "javascript" ? "javascript" : "python";
    return (
      <Box
        role="group"
        aria-label={ariaLabel}
        sx={{
          border: "1px solid",
          borderColor: isDark ? "rgba(255,255,255,0.12)" : "divider",
          borderRadius: "8px",
          overflow: "auto",
          maxHeight: height,
          cursor: "not-allowed",
          backgroundColor: isDark ? "#141A21" : "#FAFBFC",
          "& pre": { margin: "0 !important", cursor: "not-allowed" },
          "& code": { cursor: "not-allowed" },
        }}
      >
        <SyntaxHighlighter
          language={syntaxLang}
          style={isDark ? oneDark : oneLight}
          showLineNumbers
          customStyle={{
            margin: 0,
            padding: "12px 16px",
            fontSize: "13px",
            fontFamily:
              "'Fira Code', 'Cascadia Code', 'JetBrains Mono', Menlo, Monaco, 'Courier New', monospace",
            background: "transparent",
            cursor: "not-allowed",
          }}
          lineNumberStyle={{
            color: isDark ? "#4a5568" : "#94a3b8",
            minWidth: "2.5em",
            cursor: "not-allowed",
          }}
          wrapLongLines
        >
          {value || placeholder || ""}
        </SyntaxHighlighter>
      </Box>
    );
  }

  return (
    <Box
      role="group"
      aria-label={ariaLabel}
      sx={{
        border: "1px solid",
        borderColor: isDark ? "rgba(255,255,255,0.12)" : "divider",
        borderRadius: "8px",
        overflow: "hidden",
        position: "relative",
        backgroundColor: isDark ? "#141A21" : "#FAFBFC",
        "&:focus-within": {
          borderColor: "primary.main",
        },
      }}
    >
      <Editor
        height={height}
        defaultLanguage={language}
        language={language}
        value={value}
        onChange={onChange}
        options={{
          ariaLabel,
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily:
            "'Fira Code', 'Cascadia Code', 'JetBrains Mono', Menlo, Monaco, 'Courier New', monospace",
          fontLigatures: true,
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 4,
          wordWrap: "on",
          padding: { top: 12 },
          suggest: {
            showSnippets: true,
            showKeywords: true,
            showFunctions: true,
          },
          quickSuggestions: {
            other: true,
            strings: true,
            comments: false,
          },
          parameterHints: { enabled: true },
          bracketPairColorization: { enabled: true },
          renderLineHighlight: "line",
          scrollbar: {
            verticalScrollbarSize: 8,
            horizontalScrollbarSize: 8,
            alwaysConsumeMouseWheel: false,
          },
        }}
        theme={isDark ? "futureagi-dark" : "futureagi-light"}
        onMount={handleEditorDidMount}
      />
      {!value && placeholder && (
        <Box
          sx={{
            position: "absolute",
            top: 12,
            left: 64,
            color: isDark ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.3)",
            fontSize: "13px",
            fontFamily:
              "'Fira Code', 'Cascadia Code', Menlo, Monaco, 'Courier New', monospace",
            pointerEvents: "none",
            whiteSpace: "pre-wrap",
          }}
        >
          {placeholder}
        </Box>
      )}
    </Box>
  );
};

CodeEditor.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  language: PropTypes.oneOf(["python", "javascript", "json"]),
  height: PropTypes.string,
  disabled: PropTypes.bool,
  placeholder: PropTypes.string,
  ariaLabel: PropTypes.string,
};

export default CodeEditor;
