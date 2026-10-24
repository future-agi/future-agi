import React, { useMemo } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../AccordianElements";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import MessageList from "./MessageList";
import { FormSelectField } from "src/components/FormSelectField";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
import ModelOptions from "./ModelOptions";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const OptimizePrompt = ({ control, allColumns }) => {
  const { data: runPromptOptions } = useRunPromptOptions();

  const modelOptions = useMemo(
    () => {
      // @ts-ignore
      return (
        runPromptOptions?.models?.map((m) => ({
          label: m.model_name,
          value: m.model_name,
          disabled: !m.isAvailable,
        })) || []
      );
    },
    // @ts-ignore
    [runPromptOptions?.models],
  );

  return (
    <Accordion defaultExpanded>
      <AccordionSummary>Configure Prompt</AccordionSummary>
      <AccordionDetails>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          <FormTextFieldV2
            control={control}
            fieldName="promptName"
            label="Prompt Name"
            fullWidth
            placeholder="Prompt 1"
          />
          <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
            <Button
              size="small"
              color="primary"
              startIcon={<Iconify icon="solar:add-linear" />}
            >
              Import Prompt
            </Button>
          </Box>
          <MessageList control={control} allColumns={allColumns} />
          <Box sx={{ display: "flex", gap: 2, flexDirection: "column" }}>
            <Typography color="text.secondary" fontSize="12px" fontWeight={600}>
              Language Model
            </Typography>
            <FormSelectField
              size="small"
              control={control}
              fieldName="modelConfig.model_name"
              options={modelOptions}
              fullWidth
              MenuProps={{
                sx: {
                  maxHeight: "400px",
                },
              }}
            />
          </Box>
          <ModelOptions control={control} />
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

OptimizePrompt.propTypes = {
  control: PropTypes.any,
  allColumns: PropTypes.array,
  isEvaluationOpen: PropTypes.bool,
  refreshGrid: PropTypes.func,
};

export default OptimizePrompt;
