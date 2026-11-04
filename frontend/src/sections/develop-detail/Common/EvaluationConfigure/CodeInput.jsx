import React, { useEffect, useRef } from "react";
import Editor from "@monaco-editor/react";
import PropTypes from "prop-types";
import { Box, FormHelperText, Typography } from "@mui/material";
import { camelCaseToTitleCase } from "src/utils/utils";
import { useController } from "react-hook-form";
import _ from "lodash";
import HelperText from "../HelperText";
import SyntaxHighlighter from "react-syntax-highlighter";

const CodeInput = ({
  control,
  config,
  configKey,
  allColumns,
  jsonSchemas = {},
}) => {
  const editorRef = useRef(null);
  const monacoRef = useRef(null);
  const completionProviderRef = useRef(null);
  const languageConfigurationRef = useRef(null);

  const fieldName = `config.config.${configKey}`;
  const helperText =
    config?.configParamsDesc?.[configKey] ||
    config?.config_params_desc?.[configKey];

  const { field, formState } = useController({
    control,
    name: fieldName,
  });

  // Handle editor content changes
  const handleEditorChange = (value) => {
    field.onChange(value);
  };

  // Editor options
  const editorOptions = {
    selectOnLineNumbers: true,
    roundedSelection: false,
    readOnly: false,
    cursorStyle: "line",
    automaticLayout: true,
    minimap: {
      enabled: false,
    },
  };

  const { errors } = formState;

  const errorMessage = _.get(errors, fieldName)?.message || "";
  const isError = !!errorMessage;

  const handleEditorDidMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    setTimeout(() => {
      languageConfigurationRef.current =
        monaco.languages.setLanguageConfiguration("python", {
          autoClosingPairs: [
            { open: "(", close: ")" },
            { open: "[", close: "]" },
            { open: "'", close: "'" },
            { open: '"', close: '"' },
            { open: "`", close: "`" },
          ],
        });
    }, 0);
    completionProviderRef.current =
      monaco.languages.registerCompletionItemProvider("python", {
        triggerCharacters: ["{", "."],
        provideCompletionItems: (model, position) => {
          const textBeforeCursor = model.getValueInRange({
            startLineNumber: position.lineNumber,
            startColumn: position.column - 2,
            endLineNumber: position.lineNumber,
            endColumn: position.column,
          });
          if (textBeforeCursor === "{{") {
            // Build expanded suggestions including JSON paths
            const suggestions = [];

            allColumns.forEach((item) => {
              // Add base column
              suggestions.push({
                label: item.headerName,
                insertText: `${item.headerName}}}`,
                kind: monaco.languages.CompletionItemKind.Variable,
                documentation: "Insert a column variable",
              });

              // Add JSON paths for JSON-type columns
              if (item?.dataType === "json" && jsonSchemas?.[item?.field]) {
                const schema = jsonSchemas[item?.field];
                schema?.keys?.forEach((path) => {
                  suggestions.push({
                    label: `${item.headerName}.${path}`,
                    insertText: `${item.headerName}.${path}}}`,
                    kind: monaco.languages.CompletionItemKind.Property,
                    documentation: `JSON path in ${item.headerName}`,
                  });
                });
              }
            });

            return { suggestions };
          }

          return { suggestions: [] };
        },
      });
  };

  useEffect(() => {
    return () => {
      if (completionProviderRef.current) {
        completionProviderRef.current.dispose();
      }
      if (languageConfigurationRef.current) {
        languageConfigurationRef.current.dispose();
      }
      if (editorRef.current) {
        editorRef.current.dispose();
      }
    };
  }, []);

  return (
    <Box sx={{ display: "flex", gap: 1.5, flexDirection: "column" }}>
      <Box sx={{ display: "flex", gap: 0.5, flexDirection: "column" }}>
        <Typography variant="body2">
          {camelCaseToTitleCase(configKey)}
        </Typography>
        <HelperText text={helperText} />
        <SyntaxHighlighter
          useInlineStyles={false}
          codeTagProps={{
            style: {
              color: "var(--text-primary)",
              textShadow: "none",
              background: "transparent",
            },
          }}
          customStyle={{
            fontSize: "12px",
            borderRadius: "8px",
            textAlign: "left",
            border: "1px solid var(--border-default)",
            padding: "10px",
            backgroundColor: "var(--bg-neutral)",
            color: "var(--text-primary)",
            textShadow: "none",
            // fontFamily: primaryFont,
          }}
          language="python"
        >
          {`# Use f-string to access column values

var = f"{{col_name}}"`}
        </SyntaxHighlighter>
        <Typography variant="caption" color="text.secondary">
          Make user variables used in code are f-string
        </Typography>
      </Box>
      <Box
        sx={{
          border: "1px solid var(--border-default)",
          borderRadius: "8px",
          overflow: "hidden",
        }}
      >
        <Editor
          height="300px"
          defaultLanguage="python"
          value={field.value}
          onChange={handleEditorChange}
          options={editorOptions}
          theme="vs-dark"
          onMount={handleEditorDidMount}
        />
      </Box>
      {!!isError && (
        <FormHelperText sx={{ paddingLeft: 1, marginTop: 0 }} error={!!isError}>
          {errorMessage}
        </FormHelperText>
      )}
    </Box>
  );
};

CodeInput.propTypes = {
  control: PropTypes.object,
  fieldConfig: PropTypes.object,
  config: PropTypes.object,
  configKey: PropTypes.string,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
};

export default CodeInput;
