import { Editor } from "@monaco-editor/react";
import { usePersonalKeyText } from "src/components/personal-key-snippet-context";
import { NO_RECORDING_CLASS } from "src/sections/omega/code/noRecording";
import {
  Box,
  FormHelperText,
  IconButton,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import { Controller } from "react-hook-form";
import Iconify from "../iconify";
import { copyText } from "src/sections/omega/code/clipboard";
import { enqueueSnackbar } from "notistack";

export const FormCodeEditor = ({
  showError = true,
  placeholder = null,
  control,
  fieldName,
  helperText,
  onFocusInput,
  readOnly = false,
  label = "",
  required = false,
  showFormatButton = false,
  theme: customTheme = null,
  ...rest
}) => {
  const fill = usePersonalKeyText();
  const muiTheme = useTheme();
  const isDark = muiTheme.palette.mode === "dark";
  const editorTheme = customTheme || (isDark ? "vs-dark" : "vs");
  const editorRef = useRef(null);
  const copyRoot = useRef(null);
  const handleCopyClick = async (data) => {
    if (!data) return;
    const copied = await copyText(data, copyRoot.current);
    enqueueSnackbar(
      copied
        ? "Copied to clipboard"
        : "Could not copy. Select and copy the code.",
      {
        variant: copied ? "success" : "error",
      },
    );
  };

  const handleEditorMount = (editor) => {
    editorRef.current = editor;
    if (readOnly) {
      editor.updateOptions({
        readOnly: true,
        domReadOnly: true,
      });
    }

    // Optional focus listener
    if (typeof onFocusInput === "function") {
      editor.onDidFocusEditorText(() => {
        onFocusInput();
      });
    }
  };

  const handleFormatClick = () => {
    if (editorRef.current) {
      editorRef.current.getAction("editor.action.formatDocument").run();
    }
  };

  return (
    <Controller
      control={control}
      name={fieldName}
      render={({ field: { ref, ...restField }, formState: { errors } }) => {
        const errorMessage = _.get(errors, `${fieldName}.message`);
        const isError = !!errorMessage;
        const displayValue = readOnly ? fill(restField.value) : restField.value;

        return (
          <Box
            className={NO_RECORDING_CLASS}
            ref={copyRoot}
            sx={{
              position: "relative",
              display: "flex",
              flexDirection: "column",
              gap: 1,
              ...rest?.sx,
            }}
          >
            <Typography
              typography={"s2"}
              fontWeight={"fontWeightSemiBold"}
              display={"flex"}
              flexDirection={"row"}
              color={"text.secondary"}
            >
              {label}
              {required && (
                <Typography
                  position={"relative"}
                  top={-4}
                  color={"status.danger.fg"}
                >{`*`}</Typography>
              )}
            </Typography>
            <Box>
              {showFormatButton && (
                <Tooltip
                  sx={{ position: "absolute", top: 8, zIndex: 1, right: 40 }}
                  title="Format"
                  arrow
                >
                  <IconButton onClick={handleFormatClick}>
                    <Iconify
                      icon="solar:notes-linear"
                      sx={{ color: "text.disabled" }}
                    />
                  </IconButton>
                </Tooltip>
              )}
              <Tooltip
                sx={{ position: "absolute", top: 0, zIndex: 1, right: 4 }}
                title="Copy"
                arrow
              >
                <IconButton onClick={() => handleCopyClick(displayValue)}>
                  {rest?.copyIcon ? (
                    rest?.copyIcon
                  ) : (
                    <Iconify
                      icon="solar:copy-linear"
                      sx={{ color: "text.disabled" }}
                    />
                  )}
                </IconButton>
              </Tooltip>
              {restField.value === "" && placeholder && (
                <Typography
                  sx={{
                    position: "absolute",
                    top: 9,
                    left: 11,
                    color: "text.disabled",
                    fontStyle: "italic",
                    pointerEvents: "none",
                    zIndex: 1,
                    fontSize: 12,
                  }}
                >
                  {placeholder}
                </Typography>
              )}

              {/* Controlled Monaco updates also emit changes. A read-only
                  display must never write a filled credential into form state. */}
              <Editor
                {...restField}
                {...rest}
                {...(readOnly ? { onChange: undefined } : {})}
                value={displayValue}
                theme={editorTheme}
                onMount={handleEditorMount}
              />
            </Box>
            {((isError && showError) || helperText) && (
              <FormHelperText
                sx={{ marginTop: 0, marginLeft: 0 }}
                error={isError}
              >
                {errorMessage || helperText}
              </FormHelperText>
            )}
          </Box>
        );
      }}
    />
  );
};

FormCodeEditor.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string,
  helperText: PropTypes.string,
  readOnly: PropTypes.bool,
  onFocusInput: PropTypes.func,
  showError: PropTypes.bool,
  required: PropTypes.bool,
  label: PropTypes.string,
  showFormatButton: PropTypes.bool,
  placeholder: PropTypes.any,
  theme: PropTypes.string,
};
