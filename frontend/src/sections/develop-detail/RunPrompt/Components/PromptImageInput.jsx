import React, { useMemo, useRef, useState } from "react";
import PromptEditor from "../../../../components/PromptCards/PromptEditor";
import {
  extractVariableFromAllCols,
  getDropdownOptionsFromCols,
} from "../common";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import ExpandedPrompt from "src/components/PromptCards/ExpandedPrompt";
import SvgColor from "src/components/svg-color";
import { Controller } from "react-hook-form";
import ImportPromptSection from "./ImportPromptSection";

export default function PromptImageInput({
  allColumns,
  allInvalidVariables,
  watch,
  fieldPrefix = "config.messages",
  control,
  currentColId,
  jsonSchemas = {},
  derivedVariables = {},
  promptHasBeenImported,
  handleRemovePrompt,
  setOpenImportPromptModal,
}) {
  const quillRef = useRef(null);
  const cursorPosition = useRef(0);

  const messages = watch(fieldPrefix);
  const [expandPrompt, setExpandPrompt] = useState(false);

  // Preserve full column structure for proper JSON schema/derived variable handling
  const filteredColumns = useMemo(() => {
    return allColumns?.filter(
      (col) => !currentColId || col?.col?.id !== currentColId,
    );
  }, [allColumns, currentColId]);

  const existingCols = useMemo(() => {
    return extractVariableFromAllCols(
      filteredColumns,
      jsonSchemas,
      derivedVariables,
    );
  }, [filteredColumns, jsonSchemas, derivedVariables]);

  const memoizedMentionValues = useMemo(() => {
    return getDropdownOptionsFromCols(
      filteredColumns,
      jsonSchemas,
      derivedVariables,
    );
  }, [filteredColumns, jsonSchemas, derivedVariables]);

  const onSelectionChange = (range) => {
    if (!range) return;
    cursorPosition.current = range.index;
  };

  return (
    <>
      <Stack gap={2}>
        <ImportPromptSection
          control={control}
          promptHasBeenImported={promptHasBeenImported}
          handleRemovePrompt={handleRemovePrompt}
          setOpenImportPromptModal={setOpenImportPromptModal}
        >
          <Stack gap={0.25}>
            <Typography
              typography={"s1"}
              color={"text.primary"}
              fontWeight={"fontWeightMedium"}
            >
              Image Prompt
              <Typography component={"span"} color={"status.danger.fg"}>
                *
              </Typography>
            </Typography>
            <Typography
              typography={"s2"}
              color={"text.primary"}
              fontWeight={"fontWeightRegular"}
            >
              Write the prompt describing the image you want to generate
            </Typography>
          </Stack>
        </ImportPromptSection>
        <Stack
          gap={0.5}
          sx={{
            position: "relative",
          }}
        >
          <Controller
            control={control}
            name={`${fieldPrefix}.1`}
            render={({ field, fieldState: { error } }) => {
              return (
                <Box>
                  <PromptEditor
                    inputRef={field.ref}
                    placeholder="Describe the image you want to generate..."
                    appliedVariableData={existingCols}
                    prompt={field.value?.content}
                    onPromptChange={(content) => {
                      field.onChange({
                        ...field.value,
                        content,
                      });
                    }}
                    ref={quillRef}
                    onSelectionChange={onSelectionChange}
                    dropdownOptions={memoizedMentionValues}
                    showEditEmbed={false}
                    mentionEnabled={true}
                    allowVariables={true}
                    label={true}
                    expandable={true}
                    sx={{
                      minHeight: "200px",
                    }}
                  />
                  {error?.content?.message && (
                    <Typography
                      id={`${fieldPrefix}.1`}
                      variant="caption"
                      color={"error.main"}
                    >
                      {error?.content?.message}
                    </Typography>
                  )}
                </Box>
              );
            }}
          />
          <Typography
            id={"invalid-variables-message"}
            typography="s2"
            color={
              allInvalidVariables?.length > 0
                ? "status.danger.fg"
                : "text.primary"
            }
            fontWeight={"fontWeightRegular"}
          >
            Use variables from your dataset columns to dynamically generate
            different images for each row.
          </Typography>
          <IconButton
            sx={{
              position: "absolute",
              right: 10,
              bottom: 25,
            }}
            size="small"
            onClick={() => setExpandPrompt(true)}
          >
            <SvgColor
              sx={{
                height: "16px",
                width: "16px",
                bgcolor: "text.secondary",
              }}
              src="/assets/icons/ic_maximize.svg"
            />
          </IconButton>
        </Stack>
      </Stack>
      {expandPrompt && (
        <ExpandedPrompt
          open={expandPrompt}
          onClose={() => setExpandPrompt(false)}
          index={1}
          mainEditorRef={quillRef}
          defaultValue={messages?.[1]?.content}
          dropdownOptions={memoizedMentionValues}
          appliedVariableData={existingCols}
          mentionEnabled={true}
          allowVariables
          hideExpandedHeader={true}
          role={true}
        />
      )}
    </>
  );
}

PromptImageInput.propTypes = {
  allInvalidVariables: PropTypes.array,
  watch: PropTypes.func,
  allColumns: PropTypes.array,
  fieldPrefix: PropTypes.string,
  control: PropTypes.any,
  currentColId: PropTypes.string,
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  promptHasBeenImported: PropTypes.bool,
  handleRemovePrompt: PropTypes.func,
  setOpenImportPromptModal: PropTypes.func,
};
