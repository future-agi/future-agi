import { Box, Typography } from "@mui/material";
import React, { useEffect, useMemo } from "react";
import { FormCodeEditor } from "../form-code-editor";
import { useForm, useWatch } from "react-hook-form";
import { usePromptWorkbenchContext } from "src/sections/workbench/createPrompt/WorkbenchContext";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { messagesArraySchema } from "./validation";
import PropTypes from "prop-types";

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  wordWrap: "on",
  lineNumbers: "on",
  folding: true,
  minimap: { enabled: false },
  glyphMargin: false,
  lineDecorationsWidth: 0,
  renderIndentGuides: false,
  lineNumbersMinChars: 0,
  scrollBeyondLastLine: false,
  scrollbar: {
    vertical: "auto",
    horizontal: "visible",
    verticalScrollbarSize: 10,
    horizontalScrollbarSize: 10,
    alwaysConsumeMouseWheel: false,
    useShadows: false,
  },
};

const PlaceholdersList = ({ onValidationChange }) => {
  const { setPlaceholderData, placeholders, placeholderData } =
    usePromptWorkbenchContext();

  const defaultJsonSchema = `[
  {
    "type": "text",
    "message": "Enter your message here"
  }
]`;

  const allPlaceholderNames = useMemo(() => {
    const flat = placeholders.flat();
    return flat.filter((name, index) => flat.indexOf(name) === index);
  }, [placeholders]);

  const normalizeMessageKeys = (message) => {
    if (message.type === "text") {
      return message;
    }

    // For non-text types, convert camelCase keys to snake_case
    const normalizedMessage = { ...message };

    // Handle fileName -> file_name
    if (normalizedMessage.fileName && !normalizedMessage.file_name) {
      normalizedMessage.file_name = normalizedMessage.fileName;
      delete normalizedMessage.fileName;
    }

    // Handle fileUrl -> file_url
    if (normalizedMessage.fileUrl && !normalizedMessage.file_url) {
      normalizedMessage.file_url = normalizedMessage.fileUrl;
      delete normalizedMessage.fileUrl;
    }

    return normalizedMessage;
  };

  const defaultValues = useMemo(() => {
    return allPlaceholderNames.reduce((acc, placeholderName) => {
      const existingMessages = placeholderData?.[placeholderName] || [];

      if (Array.isArray(existingMessages) && existingMessages.length > 0) {
        // Normalize keys in existing messages before displaying
        const normalizedMessages = existingMessages.map(normalizeMessageKeys);
        acc[placeholderName] = JSON.stringify(normalizedMessages, null, 2);
      } else {
        acc[placeholderName] = defaultJsonSchema;
      }

      return acc;
    }, {});
  }, [allPlaceholderNames, placeholderData, defaultJsonSchema]);

  const placeholderSchema = useMemo(() => {
    const createPlaceholderSchema = (placeholderNames) => {
      const schemaFields = {};
      placeholderNames.forEach((name) => {
        schemaFields[name] = z.string().transform((val, ctx) => {
          if (val === defaultJsonSchema || val.trim() === "") {
            return null;
          }

          try {
            const parsed = JSON.parse(val);
            const result = messagesArraySchema.parse(parsed);
            return result.map(normalizeMessageKeys);
          } catch (error) {
            if (error instanceof SyntaxError) {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "Invalid JSON format",
              });
            } else if (error instanceof z.ZodError) {
              const firstError = error.errors[0];
              const messageIndex = firstError.path[0];
              const errorMessage =
                typeof messageIndex === "number"
                  ? `Message ${messageIndex + 1}: ${firstError.message}`
                  : firstError.message;

              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: errorMessage,
              });
            } else {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "Invalid data",
              });
            }
            return z.NEVER;
          }
        });
      });
      return z.object(schemaFields);
    };

    return createPlaceholderSchema(allPlaceholderNames);
  }, [allPlaceholderNames, defaultJsonSchema]);

  const {
    control,
    formState: { errors, isValid },
  } = useForm({
    resolver: zodResolver(placeholderSchema),
    defaultValues: defaultValues,
    mode: "onChange",
  });

  const formValues = useWatch({ control });

  useEffect(() => {
    onValidationChange?.(!isValid);
  }, [isValid, onValidationChange]);

  useEffect(() => {
    if (!formValues || Object.keys(formValues).length === 0) return;

    const updatedData = Object.entries(formValues).reduce(
      (acc, [placeholderName, value]) => {
        if (!value || value.trim() === "" || value === defaultJsonSchema) {
          return acc;
        }

        try {
          const parsed = JSON.parse(value);
          const validatedMessages = messagesArraySchema.parse(parsed);
          const normalizedMessages =
            validatedMessages.map(normalizeMessageKeys);

          acc[placeholderName] = normalizedMessages;
        } catch {
          acc[placeholderName] = [];
        }

        return acc;
      },
      {},
    );

    setPlaceholderData((prev) => ({
      ...prev,
      ...updatedData,
    }));
  }, [formValues, defaultJsonSchema, setPlaceholderData]);

  if (!allPlaceholderNames.length) return null;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        width: "100%",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          flex: 1,
          overflowY: "auto",
          minHeight: 0,
        }}
      >
        {allPlaceholderNames.map((placeholderName) => (
          <Box
            key={placeholderName}
            width="100%"
            display="flex"
            flexDirection="column"
            gap={0}
          >
            <Box
              p={1}
              px={1}
              sx={{
                backgroundColor: "background.neutral",
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography variant="s1" fontWeight="fontWeightRegular">
                {placeholderName}
              </Typography>
            </Box>

            <Box
              sx={{
                border: "1px solid",
                borderColor: errors[placeholderName]
                  ? "status.danger.border"
                  : "divider",
                "& .monaco-editor .lines-content.monaco-editor-background": {
                  backgroundColor: "var(--bg-paper) !important",
                },
              }}
            >
              <FormCodeEditor
                height="250px"
                defaultLanguage="json"
                options={{
                  ...editorOptions,
                  lineNumbers: "off",
                  lineDecorationsWidth: 0,
                  lineNumbersMinChars: 2,
                  glyphMargin: false,
                }}
                theme="xcode-default"
                language="json"
                control={control}
                fieldName={placeholderName}
                className="json-editor"
                showError={false}
                sx={{ gap: 0 }}
                showFormatButton
              />
            </Box>
            <HelperText
              text={errors[placeholderName]?.message}
              sx={{ color: "status.danger.fg" }}
            />
          </Box>
        ))}
      </Box>
    </Box>
  );
};

PlaceholdersList.propTypes = {
  onValidationChange: PropTypes.func,
};

export default PlaceholdersList;
