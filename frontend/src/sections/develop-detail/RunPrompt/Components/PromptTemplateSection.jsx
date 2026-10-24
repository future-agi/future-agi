import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import ImportPromptSection from "./ImportPromptSection";
import PromptTemplatesSection from "../PromptTemplatesSection";
import PropTypes from "prop-types";

export default function PromptTemplateSection({
  control,
  promptHasBeenImported,
  handleRemovePrompt,
  setOpenImportPromptModal,
  allInvalidVariables,
  watch,
  setOpenGeneratePromptDrawer,
  setOpenImprovePromptDrawer,
  allColumns,
  jsonSchemas = {},
  derivedVariables = {},
  setValue,
  errors,
  clearErrors,
}) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderRadius: "4px",
        borderColor: "divider",
        padding: (theme) => theme.spacing(1, 2, 2, 2),
        display: "flex",
        flexDirection: "column",
        gap: 2,
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
            typography="s1"
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Prompt template
            <Typography
              component={"span"}
              sx={{
                color: "status.danger.fg",
              }}
            >
              *
            </Typography>
          </Typography>
          <Typography
            sx={{
              color: "text.primary",
              fontWeight: "fontWeightRegular",
              typography: "s2",
            }}
          >
            Template for the prompt to be sent to the language model
          </Typography>
        </Stack>
      </ImportPromptSection>

      {/* <FormSearchSelectFieldControl
                  control={control}
                  fieldName="config.outputFormat"
                  size="small"
                  label="Output Type"
                  required
                  fullWidth
                  options={[
                    ...(Array.isArray(runPromptOptions?.outputFormats)
                      ? runPromptOptions.outputFormats
                      : []),
                  ]}
                /> */}

      <PromptTemplatesSection
        allInvalidVariables={allInvalidVariables}
        watch={watch}
        onOpenGeneratePromptDrawer={({ state, index }) => {
          setOpenGeneratePromptDrawer({
            index,
            state,
          });
        }}
        onOpenImprovePromptDrawer={({ state, index }) => {
          setOpenImprovePromptDrawer({
            index,
            state,
          });
        }}
        control={control}
        allColumns={allColumns}
        jsonSchemas={jsonSchemas}
        derivedVariables={derivedVariables}
        setValue={setValue}
        errors={errors}
        clearErrors={clearErrors}
      />
    </Box>
  );
}

PromptTemplateSection.propTypes = {
  control: PropTypes.object,
  promptHasBeenImported: PropTypes.bool,
  handleRemovePrompt: PropTypes.func,
  setOpenImportPromptModal: PropTypes.func,
  allInvalidVariables: PropTypes.array,
  watch: PropTypes.func,
  setOpenGeneratePromptDrawer: PropTypes.func,
  setOpenImprovePromptDrawer: PropTypes.func,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
  derivedVariables: PropTypes.object,
  setValue: PropTypes.func,
  errors: PropTypes.object,
  clearErrors: PropTypes.func,
};
