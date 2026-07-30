import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../AccordianElements";
import InputSection from "src/sections/common/SliderRow/InputSection";
import {
  Box,
  Typography,
  useTheme,
  Divider,
  Switch,
  Button,
  Stack,
} from "@mui/material";
import ConfigTool from "src/sections/develop-detail/RunPrompt/ToolConfig/ConfigTool";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
import { ShowComponent } from "src/components/show";
import { DEFAULT_MODEL_PARAMS, generateNMarks, TOOLTIP_OBJ } from "./common";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { useController } from "react-hook-form";
import CreateResponseSchema from "src/components/custom-model-options/CreateResponseSchema";
import _ from "lodash";
import SliderRowState from "../../common/SliderRow/SliderRowState";
import { BOOLEAN_VALUE_OPTIONS } from "../../../utils/constants";
import { FormSearchSelectFieldState } from "../../../components/FromSearchSelectField";
import CustomTooltip from "../../../components/tooltip";
import SvgColor from "src/components/svg-color/svg-color";

const defaultMenus = [
  { value: "text", label: "Text" },
  { value: "json_object", label: "JSON" },
  { value: "none", label: "None" },
];

export default function ModelOptionsState({
  responseSchema = [],
  setModelParameters,
  modelParameters,
  hideAccordion,
  hideTools,
  modelResponseFormat,
  control,
  fieldNamePrefix,
  reasoning,
  reasoningState,
  setReasoningState,
  onApply,
  onClear,
  showActions,
  disableActions = false,
}) {
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

  const menuItems = useMemo(() => {
    const menus = [...defaultMenus];

    responseSchema?.forEach((item) => {
      menus.push({ label: item.name, value: item.id });
    });

    modelResponseFormat?.forEach((item) => {
      const exists = menus.some((m) => m.value === item.value);
      if (!exists) {
        menus.push({
          label: _.startCase(item.value),
          value: item.value,
        });
      }
    });

    return menus;
  }, [responseSchema, modelResponseFormat]);

  const updateSliderParameter = (index, value) => {
    setModelParameters((prev) => ({
      ...prev,
      sliders: prev.sliders?.map((item, i) =>
        i === index ? { ...item, value } : item,
      ),
    }));
  };

  const updateReasonSliderParameter = (index, value) => {
    setReasoningState((prev) => ({
      ...prev,
      sliders: prev.sliders?.map((item, i) =>
        i === index ? { ...item, value } : item,
      ),
    }));
  };

  const updateBooleanParameter = (index, value) => {
    setModelParameters((prev) => ({
      ...prev,
      booleans: prev.booleans?.map((item, i) =>
        i === index ? { ...item, value } : item,
      ),
    }));
  };

  const updateDropdownParameter = (index, value) => {
    setModelParameters((prev) => ({
      ...prev,
      dropdowns: prev.dropdowns?.map((item, i) =>
        i === index ? { ...item, value } : item,
      ),
    }));
  };

  const updateReasonDropdownParameter = (index, value) => {
    setReasoningState((prev) => ({
      ...prev,
      dropdowns: prev.dropdowns?.map((item, i) =>
        i === index ? { ...item, value } : item,
      ),
    }));
  };

  const renderInfo = () => {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(1.5),
        }}
      >
        <Typography
          typography={"s2_1"}
          fontWeight={"fontWeightSemiBold"}
          sx={{
            color: "text.primary",
          }}
        >
          Model options
        </Typography>
        {(Array.isArray(modelParameters?.sliders)
          ? modelParameters.sliders
          : DEFAULT_MODEL_PARAMS
        ).map((item, index) => (
          <SliderRowState
            key={item?.id}
            tooltipText={
              TOOLTIP_OBJ[item.label] ? TOOLTIP_OBJ[item.label] : null
            }
            onChange={(value) => updateSliderParameter(index, value)}
            label={item?.label}
            value={item?.value}
            min={item?.min}
            max={item?.max}
            step={item?.step}
            marks={generateNMarks(item?.min, item?.max)}
            sx={sliderMarkStyles}
            disabled={item?.disabled}
          />
        ))}
        <ShowComponent condition={modelResponseFormat?.length > 0}>
          <Box sx={{ display: "flex", justifyContent: "space-between" }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography
                variant="s2"
                fontWeight={"fontWeightMedium"}
                color={"text.primary"}
              >
                Response Format
              </Typography>
            </Box>
            <FormSearchSelectFieldControl
              sx={{
                "& .MuiInputBase-input": {
                  height: "26px",
                  paddingY: "2px",
                },
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
        </ShowComponent>
        {modelParameters?.booleans?.map((item, index) => (
          <Box
            key={item?.label}
            sx={{ display: "flex", justifyContent: "space-between" }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography
                variant="s3"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
              >
                {_.startCase(item?.label)}
              </Typography>
            </Box>
            <FormSearchSelectFieldState
              key={item?.label}
              value={item?.value}
              onChange={(e) => {
                updateBooleanParameter(index, e?.target?.value);
              }}
              size="small"
              placeholder="Select"
              sx={{
                width: 200,
                "& .MuiInputBase-input": {
                  height: "26px",
                  paddingY: "2px",
                },
                "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline":
                  {
                    borderColor: "divider",
                  },
              }}
              options={BOOLEAN_VALUE_OPTIONS}
            />
          </Box>
        ))}
        {modelParameters?.dropdowns?.map((item, index) => (
          <Box
            key={item?.label}
            sx={{ display: "flex", justifyContent: "space-between" }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography
                variant="s3"
                fontWeight={"fontWeightMedium"}
                color="text.primary"
              >
                {_.startCase(item?.label)}
              </Typography>
            </Box>
            <FormSearchSelectFieldState
              key={item?.label}
              value={item?.value}
              onChange={(e) => {
                updateDropdownParameter(index, e?.target?.value);
              }}
              size="small"
              placeholder="Select"
              sx={{
                width: 200,
                "& .MuiInputBase-input": {
                  height: "26px",
                  paddingY: "2px",
                },
                "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline":
                  {
                    borderColor: "divider",
                  },
              }}
              options={item?.options?.map((op) => ({
                label: _.startCase(op),
                value: op,
              }))}
            />
          </Box>
        ))}
        {reasoning && (
          <>
            <Divider sx={{ borderColor: "divider" }} />
            <Typography
              typography={"s2_1"}
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              Reasoning
            </Typography>
            {reasoningState?.sliders?.map((item, index) => (
              <SliderRowState
                key={item?.label}
                label={item?.label}
                value={item?.value}
                onChange={(value) => updateReasonSliderParameter(index, value)}
                min={item?.min}
                max={item?.max}
                step={item?.step}
                marks={generateNMarks(item?.min, item?.max)}
                sx={sliderMarkStyles}
                disabled={item?.disabled}
              />
            ))}
            {reasoningState?.dropdowns?.map((item, index) => (
              <Box
                key={item?.label}
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <Typography
                  variant="s3"
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                >
                  {_.startCase(item?.label)}
                </Typography>
                <FormSearchSelectFieldState
                  showClear={false}
                  value={item?.value ?? ""}
                  onChange={(e) => {
                    updateReasonDropdownParameter(index, e?.target?.value);
                  }}
                  size="small"
                  placeholder="Select"
                  sx={{
                    width: 200,
                    "& .MuiInputBase-input": {
                      height: "26px",
                      paddingY: "2px",
                    },
                    "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline":
                      {
                        borderColor: "divider",
                      },
                  }}
                  options={item?.options?.map((op) => ({
                    label: _.startCase(op),
                    value: op,
                  }))}
                />
              </Box>
            ))}
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Stack direction={"row"} alignItems={"center"} gap={1}>
                <Typography
                  typography="s3"
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                >
                  Show reasoning process
                </Typography>
                <CustomTooltip
                  type={"black"}
                  size="small"
                  show
                  arrow
                  title="Reasoning visibility depends on model support. Some models may not display it."
                >
                  <SvgColor
                    sx={{
                      height: "16px",
                      width: "16px",
                    }}
                    src={"/assets/icons/ic_info.svg"}
                  />
                </CustomTooltip>
              </Stack>
              <Switch
                checked={reasoningState?.showReasoningProcess ?? false}
                onChange={(e) =>
                  setReasoningState((prev) => ({
                    ...prev,
                    showReasoningProcess: e.target.checked,
                  }))
                }
              />
            </Box>
          </>
        )}
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
        {showActions && (
          <Box
            sx={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 1,
              mt: 0.5,
            }}
          >
            <Button
              variant="outlined"
              size="small"
              onClick={onClear}
              disabled={disableActions}
              sx={{
                borderColor: "divider",
                color: "text.primary",
                textTransform: "none",
              }}
            >
              Clear
            </Button>
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={onApply}
              disabled={disableActions}
              sx={{
                textTransform: "none",
              }}
            >
              Apply
            </Button>
          </Box>
        )}
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
}

ModelOptionsState.propTypes = {
  responseSchema: PropTypes.array,
  setValue: PropTypes.func,
  control: PropTypes.object,
  fieldNamePrefix: PropTypes.string,
  hideAccordion: PropTypes.bool,
  hideTools: PropTypes.bool,
  modelParams: PropTypes.array,
  modelResponseFormat: PropTypes.array,
  setModelParameters: PropTypes.func,
  modelParameters: PropTypes.array,
  reasoning: PropTypes.object,
  reasoningState: PropTypes.object,
  setReasoningState: PropTypes.func,
  onApply: PropTypes.func,
  onClear: PropTypes.func,
  showActions: PropTypes.bool,
  disableActions: PropTypes.bool,
};
