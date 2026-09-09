import React from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import PromptTemplateMessageList from "../PromptTemplateMessageList";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";
import PropTypes from "prop-types";

export default function LLMPrompt({
  control,
  index,
  watchedMessages,
  allColumns,
  jsonSchemas = {},
  derivedVariables = {},
  onGeneratePrompt,
  onImprovePrompt,
  handleRemoveMessage,
  allInvalidVariables,
  append,
  jinjaMode = false,
}) {
  return (
    <>
      <Stack direction={"column"}>
        <PromptTemplateMessageList
          control={control}
          promptField={`promptConfig.${index}`}
          messages={watchedMessages}
          remove={handleRemoveMessage}
          allColumns={allColumns}
          jsonSchemas={jsonSchemas}
          derivedVariables={derivedVariables}
          onGeneratePrompt={onGeneratePrompt}
          onImprovePrompt={onImprovePrompt}
          jinjaMode={jinjaMode}
        />
        <Typography
          id={`invalid-variables-message.${index}`}
          typography="s2"
          color={allInvalidVariables?.length > 0 ? "red.500" : "text.primary"}
          fontWeight={"fontWeightRegular"}
          sx={{
            mt: (theme) => theme.spacing(1),
          }}
        >
          Use the variables corresponding to the column names to optimize the
          run prompt for best performance.
        </Typography>
      </Stack>
      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <Button
          size="medium"
          startIcon={<Iconify icon="material-symbols:add" />}
          onClick={() => {
            append({ id: getRandomId(), role: "user", content: "" });
          }}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            padding: "6px 16px",
            color: "text.primary",
          }}
        >
          <Typography typography="s1" fontWeight={"fontWeightMedium"}>
            Add Message
          </Typography>
        </Button>
      </Box>
    </>
  );
}

LLMPrompt.propTypes = {
  control: PropTypes.any.isRequired, // react-hook-form control
  index: PropTypes.number.isRequired,
  watchedMessages: PropTypes.arrayOf(PropTypes.object).isRequired,
  allColumns: PropTypes.arrayOf(PropTypes.object),
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  onGeneratePrompt: PropTypes.func,
  onImprovePrompt: PropTypes.func,
  handleRemoveMessage: PropTypes.func.isRequired,
  allInvalidVariables: PropTypes.arrayOf(PropTypes.string),
  append: PropTypes.func.isRequired,
  jinjaMode: PropTypes.bool,
};
