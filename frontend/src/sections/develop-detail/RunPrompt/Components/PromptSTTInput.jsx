import React, { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { getRandomId } from "src/utils/utils";
import ImportPromptSection from "./ImportPromptSection";

export default function PromptSTTInput({
  allColumns,
  control,
  setValue,
  getValues,
  currentColId,
  messageFieldPrefix = "config.messages",
  fieldPrefix = "config.voiceInputColumn",
  promptHasBeenImported,
  handleRemovePrompt,
  setOpenImportPromptModal,
}) {
  const audioColumns = useMemo(() => {
    return allColumns
      ?.filter(
        (col) =>
          col?.dataType === "audio" &&
          (!currentColId || col?.col?.id !== currentColId),
      )
      ?.map((col) => ({
        label: col?.headerName,
        value: col?.col?.id ?? col?.id,
      }));
  }, [allColumns, currentColId]);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        padding: (theme) => theme.spacing(1.5),
        display: "flex",
        flexDirection: "column",
        gap: 1.25,
      }}
    >
      <ImportPromptSection
        control={control}
        promptHasBeenImported={promptHasBeenImported}
        handleRemovePrompt={handleRemovePrompt}
        setOpenImportPromptModal={setOpenImportPromptModal}
      >
        <Stack gap={0.25}>
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Voice Input
            <Typography component={"span"} color={"status.danger.fg"}>
              *
            </Typography>
          </Typography>
          <Typography
            typography={"s2"}
            fontWeight={"fontWeightRegular"}
            color={"text.primary"}
          >
            Choose the input that you want to transcribe speech for
          </Typography>
        </Stack>
      </ImportPromptSection>
      <FormSearchSelectFieldControl
        control={control}
        fieldName={fieldPrefix}
        size={"small"}
        label={"Column"}
        placeholder={"Select Column"}
        options={audioColumns}
        onChange={(e) => {
          const label = e?.target?.option?.label ?? "";
          const newText = `{{${label}}}`;

          // Always read messages safely
          const currentMessages = Array.isArray(getValues(messageFieldPrefix))
            ? getValues(messageFieldPrefix)
            : [];

          const filteredMessages = [...currentMessages].slice(0, 2);

          // Ensure index 0 exists and is system
          if (!filteredMessages[0] || filteredMessages[0].role !== "system") {
            filteredMessages.unshift({
              id: getRandomId(),
              role: "system",
              content: [{ type: "text", text: "" }],
            });
          }

          // Always ensure index 1 is user
          filteredMessages[1] = {
            id: filteredMessages[1]?.id ?? getRandomId(),
            role: "user",
            content: [{ type: "text", text: newText }],
          };

          // Keep only first 2
          const finalMessages = filteredMessages.slice(0, 2);

          setValue(messageFieldPrefix, finalMessages, {
            shouldDirty: true,
            shouldValidate: true,
          });
        }}
      />
    </Box>
  );
}
PromptSTTInput.propTypes = {
  allColumns: PropTypes.array,
  control: PropTypes.object,
  setValue: PropTypes.func,
  getValues: PropTypes.func,
  currentColId: PropTypes.string,
  messageFieldPrefix: PropTypes.string,
  fieldPrefix: PropTypes.string,
  promptHasBeenImported: PropTypes.bool,
  handleRemovePrompt: PropTypes.func,
  setOpenImportPromptModal: PropTypes.func,
};
