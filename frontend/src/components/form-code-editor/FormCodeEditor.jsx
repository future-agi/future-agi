import { Editor } from "@monaco-editor/react";
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
import { copyToClipboard } from "src/utils/utils";
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
  const muiTheme = useTheme();
  const isDark = muiTheme.palette.mode === "dark";
  const editorTheme = customTheme || (isDark ? "vs-dark" : "vs");
  const editorRef = useRef(null);
  const handleCopyClick = (data) => {
    if (data) {
      copyToClipboard(data);
      enqueueSnackbar("Copied to clipboard", {
        variant: "success",
      });
    }
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

        return (
          <Box
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
                  color={"red.500"}
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
                      icon="material-symbols:format-align-left"
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
                <IconButton
                  aria-label={label ? `Copy ${label}` : "Copy code"}
                  onClick={() => handleCopyClick(restField.value)}
                >
                  {rest?.copyIcon ? (
                    rest?.copyIcon
                  ) : (
                    <Iconify
                      icon="basil:copy-outline"
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

              <Editor
                {...restField}
                {...rest}
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
