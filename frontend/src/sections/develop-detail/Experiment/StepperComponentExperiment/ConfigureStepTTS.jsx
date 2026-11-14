import {
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Stack,
  Typography,
} from "@mui/material";
import React from "react";
import { useFieldArray, useWatch } from "react-hook-form";
import PropTypes from "prop-types";
import { getDefaultPromptConfigByModelType } from "../common";
import ConfigureStepTTSItem from "./ConfigureStepTTSItem";
import { Accordion } from "../../AccordianElements";
import Iconify from "src/components/iconify";

const ConfigureStepTTS = ({
  control,
  allColumns,
  setValue,
  clearErrors,
  errors,
  watch,
  jsonSchemas,
  getValues,
  unregister,
  derivedVariables,
}) => {
  const { fields, append, remove } = useFieldArray({
    control,
    name: "promptConfig",
  });
  const watchModelType = useWatch({
    control,
    name: `promptConfig.${0}.experimentType`,
  });
  return (
    <Accordion
      defaultExpanded={true}
      disableGutters
      sx={{
        border: "1px solid",
        borderColor: "border.default",
        borderRadius: "8px !important",
        boxShadow: "none",
        backgroundColor: "background.default",
        "&:before": { display: "none" },
        "&.Mui-expanded": {
          margin: 0,
          backgroundColor: "background.neutral",
        },
      }}
    >
      <AccordionSummary
        expandIcon={
          <Iconify
            icon="solar:alt-arrow-up-linear"
            width={20}
            height={20}
            color="text.primary"
          />
        }
        sx={{
          px: 2,
          py: 1,

          minHeight: "auto !important",
          "& .MuiAccordionSummary-content": {
            margin: 0,
          },
          "& .MuiAccordionSummary-expandIconWrapper": {
            transform: "rotate(-180deg)",
            transition: "transform 0.2s",
          },
          "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
            transform: "rotate(0deg)",
          },
        }}
      >
        <Box>
          <Typography
            sx={{ display: "flex", flexDirection: "row", alignItems: "center" }}
            typography="s1_2"
            fontWeight={"fontWeightMedium"}
          >
            Prompts
            <Typography type="span" sx={{ color: "status.danger.fg" }}>
              *
            </Typography>
          </Typography>
          <Typography
            typography={"s2_1"}
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
          >
            Create a prompt or import in this section and{" "}
            <Typography
              typography={"s2"}
              fontWeight={"fontWeightSemiBold"}
              component={"span"}
            >
              Generate tts responses
            </Typography>{" "}
            for the experiment
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 2, py: 2 }}>
        <Stack spacing={2}>
          {fields.map((field, index) => (
            <ConfigureStepTTSItem
              key={field.id}
              index={index}
              field={field}
              watchModelType={watchModelType}
              allColumns={allColumns}
              setValue={setValue}
              clearErrors={clearErrors}
              errors={errors}
              watch={watch}
              control={control}
              derivedVariables={derivedVariables}
              jsonSchemas={jsonSchemas}
              getValues={getValues}
              unregister={unregister}
              totalPrompts={fields.length}
              remove={remove}
            />
          ))}
        </Stack>
        <Button
          variant="outlined"
          onClick={() => {
            append(getDefaultPromptConfigByModelType(watchModelType));
          }}
          color={"primary"}
          startIcon={<Iconify icon="solar:add-linear" />}
          size="medium"
          sx={{ width: "300px", mt: 2 }}
        >
          Add Prompt
        </Button>
      </AccordionDetails>
    </Accordion>
  );
};

export default ConfigureStepTTS;

ConfigureStepTTS.propTypes = {
  control: PropTypes.object.isRequired,
  allColumns: PropTypes.array,
  setValue: PropTypes.func,
  clearErrors: PropTypes.func,
  errors: PropTypes.object,
  watch: PropTypes.func,
  jsonSchemas: PropTypes.any,
  getValues: PropTypes.func,
  unregister: PropTypes.func,
  derivedVariables: PropTypes.object,
};
