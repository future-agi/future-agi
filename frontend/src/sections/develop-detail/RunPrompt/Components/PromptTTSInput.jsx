import React, { useMemo, useRef, useState } from "react";
import PromptEditor from "../../../../components/PromptCards/PromptEditor";
import { PromptEditorPlaceholder } from "../../../../utils/constants";
import {
  extractVariableFromAllCols,
  getDropdownOptionsFromCols,
} from "../common";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import ExpandedPrompt from "src/components/PromptCards/ExpandedPrompt";
import SvgColor from "src/components/svg-color";
import { Controller } from "react-hook-form";
import { ShowComponent } from "src/components/show";

export default function PromptTTSInput({
  allColumns,
  allInvalidVariables,
  watch,
  fieldPrefix = "config.messages",
  control,
  currentColId,
  title = null,
  onDelete = null,
  jsonSchemas = {},
  derivedVariables = {},
  promptHasBeenImported: _promptHasBeenImported,
  handleRemovePrompt: _handleRemovePrompt,
  setOpenImportPromptModal: _setOpenImportPromptModal,
  jinjaMode = false,
}) {
  const quillRef = useRef(null);
  const cursorPosition = useRef(0);
  const messages = watch(fieldPrefix);
  const [expandPrompt, setExpandPrompt] = useState(false);
  // Preserve full column structure for proper JSON schema/derived variable handling
  const nonAudioColumns = useMemo(() => {
    return allColumns?.filter(
      (col) =>
        col?.dataType !== "audio" &&
        (!currentColId || col?.col?.id !== currentColId),
    );
  }, [allColumns, currentColId]);

  const existingCols = useMemo(() => {
    return extractVariableFromAllCols(
      nonAudioColumns,
      jsonSchemas,
      derivedVariables,
    );
  }, [nonAudioColumns, jsonSchemas, derivedVariables]);

  const memoizedMentionValues = useMemo(() => {
    return getDropdownOptionsFromCols(
      nonAudioColumns,
      jsonSchemas,
      derivedVariables,
    );
  }, [nonAudioColumns, jsonSchemas, derivedVariables]);
  const onSelectionChange = (range) => {
    if (!range) return;
    cursorPosition.current = range.index;
  };
  return (
    <>
      <Stack gap={2}>
        {title ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Typography
              typography={"s1"}
              color={"text.primary"}
              fontWeight={"fontWeightMedium"}
            >
              {title}
            </Typography>
            <ShowComponent condition={onDelete !== null}>
              <IconButton size="small" onClick={onDelete}>
                <SvgColor
                  sx={{
                    height: "24px",
                    width: "24px",
                  }}
                  src="/assets/icons/ic_close.svg"
                />
              </IconButton>
            </ShowComponent>
          </Box>
        ) : (
          <Stack>
            <Typography
              typography={"s1"}
              color={"text.primary"}
              fontWeight={"fontWeightMedium"}
            >
              Prompt Input
              <Typography component={"span"} color={"red.500"}>
                *
              </Typography>
            </Typography>
            <Typography
              typography={"s2"}
              color={"text.secondary"}
              fontWeight={"fontWeightRegular"}
            >
              Write the input that you want to synthesize speech for
            </Typography>
          </Stack>
        )}
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
                    placeholder={PromptEditorPlaceholder["user"]}
                    appliedVariableData={existingCols}
                    prompt={field.value?.content} // connect to RHF
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
                    jinjaMode={jinjaMode}
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
            color={allInvalidVariables?.length > 0 ? "red.500" : "text.primary"}
            fontWeight={"fontWeightRegular"}
          >
            Use the variables corresponding to the column names to optimize the
            run prompt for best performance.
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
          jinjaMode={jinjaMode}
        />
      )}
    </>
  );
}

PromptTTSInput.propTypes = {
  allInvalidVariables: PropTypes.array,
  watch: PropTypes.func,
  allColumns: PropTypes.array,
  fieldPrefix: PropTypes.string,
  control: PropTypes.any,
  currentColId: PropTypes.string,
  title: PropTypes.string,
  onDelete: PropTypes.func,
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  promptHasBeenImported: PropTypes.bool,
  handleRemovePrompt: PropTypes.func,
  setOpenImportPromptModal: PropTypes.func,
  jinjaMode: PropTypes.bool,
};
