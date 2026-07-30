import React, { useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../AccordianElements";
import InputSection from "src/sections/common/SliderRow/InputSection";
import { Box, Typography, useTheme, Divider } from "@mui/material";
import PropTypes from "prop-types";
import SliderRow from "../../common/SliderRow/SliderRow";
import ConfigTool from "src/sections/develop-detail/RunPrompt/ToolConfig/ConfigTool";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
import { ShowComponent } from "src/components/show";
import { generateNMarks } from "./common";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { useController } from "react-hook-form";
import CreateResponseSchema from "src/components/custom-model-options/CreateResponseSchema";

const ModelOptions = ({
  responseSchema = [],
  control,
  fieldNamePrefix,
  hideAccordion,
  hideTools,
}) => {
  const theme = useTheme();
  const sliderMarkStyles = {
    "& .MuiSlider-mark": {
      backgroundColor: "action.selected",
      height: theme.spacing(0.5),
      width: theme.spacing(0.25),
    },
  };
  const { data: runPromptOptions } = useRunPromptOptions();
  const [showCreate, setShowCreate] = useState(false);
  const { field } = useController({
    name: `${fieldNamePrefix}.responseFormat`,
    control,
  });
  const defaultMenus = [
    { value: "text", label: "Text" },
    { value: "json_object", label: "JSON" },
    { value: "none", label: "None" },
  ];
  const menuItems = useMemo(() => {
    const menus = [...defaultMenus];
    responseSchema.forEach((item) => {
      menus.push({ label: item.name, value: item.id });
    });
    return menus;
  }, [responseSchema]);
  const renderInfo = () => {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(1.5),
        }}
      >
        <SliderRow
          label="Temperature"
          control={control}
          fieldName={`${fieldNamePrefix}.temperature`}
          min={0}
          max={1}
          step={0.1}
          marks={generateNMarks(0, 1)}
          sx={sliderMarkStyles}
        />
        <SliderRow
          label="Top P"
          control={control}
          fieldName={`${fieldNamePrefix}.topP`}
          min={0}
          max={1}
          marks={generateNMarks(0, 1)}
          step={0.1}
          sx={sliderMarkStyles}
        />
        <SliderRow
          label="Max Tokens"
          control={control}
          fieldName={`${fieldNamePrefix}.maxTokens`}
          min={1}
          max={20000}
          step={1}
          marks={generateNMarks(1, 20000)}
          sx={sliderMarkStyles}
        />
        <SliderRow
          label="Presence Penalty"
          control={control}
          fieldName={`${fieldNamePrefix}.presencePenalty`}
          min={0}
          max={2}
          step={0.1}
          marks={generateNMarks(0, 2)}
          sx={sliderMarkStyles}
        />
        <SliderRow
          label="Frequency Penalty"
          control={control}
          fieldName={`${fieldNamePrefix}.frequencyPenalty`}
          min={0}
          max={2}
          step={0.1}
          marks={generateNMarks(0, 2)}
          sx={sliderMarkStyles}
        />
        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography
              variant="s2"
              fontWeight={"fontWeightMedium"}
              color={"text.primary"}
            >
              Response Format
            </Typography>
            {/* <Tooltip  title="Format in which you want the prompt output" arrow>
              <Iconify icon="solar:info-circle-bold" color="text.disabled" />
            </Tooltip> */}
          </Box>
          <FormSearchSelectFieldControl
            sx={{
              width: theme.spacing(148 / 8),
              "& .MuiSelect-select": {
                padding: theme.spacing(0.5, 1),
              },
            }}
            showClear={false}
            size="small"
            control={control}
            fieldName={`${fieldNamePrefix}.responseFormat`}
            options={menuItems}
            createLabel="Create new schema"
            onCreateLabel={() => {
              setShowCreate(true);
            }}
          />
        </Box>
        <ShowComponent condition={!hideTools}>
          <Box sx={{ marginBottom: "10px" }}>
            <Box
              sx={{
                display: "flex",
                gap: 2,
                flexDirection: "column",
                position: "relative",
              }}
            >
              <InputSection
                label="Tool Choice"
                icon="/assets/icons/ic_info.svg"
                tooltipText="If your application should call a function"
              >
                {/* <FormSelectField
                  control={control}
                  fieldName={`${fieldNamePrefix}.toolChoice`}
                  size="small"
                  onChange={(e) => {
                    setValue?.(`${fieldNamePrefix}.toolChoice`, e.target.value);
                  }}
                  options={[
                    ...(runPromptOptions?.tool_choices || []),
                    { value: "none", label: "None" },
                  ]}
                  sx={{ width: 200 }}
                /> */}
                <FormSearchSelectFieldControl
                  control={control}
                  fieldName={`${fieldNamePrefix}.toolChoice`}
                  size="small"
                  showClear={false}
                  sx={{
                    width: theme.spacing(148 / 8),
                    "& .MuiSelect-select": {
                      padding: theme.spacing(0.5, 1),
                    },
                  }}
                  options={[
                    ...(runPromptOptions?.tool_choices || []),
                    { value: "none", label: "None" },
                  ]}
                />
              </InputSection>
              <Divider
                sx={{
                  color: "divider",
                }}
              />
              <ConfigTool
                control={control}
                fieldName={`${fieldNamePrefix}.tools`}
              />
            </Box>
          </Box>
        </ShowComponent>
        <CreateResponseSchema
          open={showCreate}
          onClose={() => {
            setShowCreate(false);
          }}
          setValue={(value) => {
            field.onChange(value);
          }}
        />
      </Box>
    );
  };

  if (hideAccordion) {
    return renderInfo();
  }

  return (
    <Accordion
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        padding: "16px",
      }}
    >
      <AccordionSummary
        sx={{
          flexDirection: "row",
          minHeight: "unset",
          padding: 0,
          "& .MuiAccordionSummary-content": {
            padding: 0,
            margin: 0,
          },
          "& .MuiAccordionSummary-expandIconWrapper": {
            transform: "rotate(90deg)",
            "& > svg": {
              color: "text.primary",
            },
          },
          "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
            transform: "rotate(270deg)",
          },
        }}
      >
        <Typography
          variant="s1"
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          Model parameters
        </Typography>
      </AccordionSummary>
      <AccordionDetails
        sx={{
          padding: 0,
          margin: 0,
          marginTop: "16px",
        }}
      >
        {renderInfo()}
      </AccordionDetails>
    </Accordion>
  );
};

ModelOptions.propTypes = {
  responseSchema: PropTypes.array,
  setValue: PropTypes.func,
  control: PropTypes.object,
  fieldNamePrefix: PropTypes.string,
  hideAccordion: PropTypes.bool,
  hideTools: PropTypes.bool,
};

export default ModelOptions;
