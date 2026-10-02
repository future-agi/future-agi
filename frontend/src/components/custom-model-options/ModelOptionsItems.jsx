import { Box, Divider, Stack, Switch, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
// import SliderRow from "src/sections/common/SliderRow/SliderRow";
import SliderRow from "./SliderRow/SliderRow";
import CreateResponseSchema from "./CreateResponseSchema";
import { useController } from "react-hook-form";
import { FormSearchSelectFieldControl } from "../FromSearchSelectField";
import { generateNMarks } from "src/sections/develop-detail/Common/common";
import { DEFAULT_MODEL_PARAMS } from "../../sections/develop-detail/Common/common";
import { ShowComponent } from "../show/ShowComponent";
import _ from "lodash";
import { BOOLEAN_VALUE_OPTIONS } from "../../utils/constants";
import CustomAudioDialog from "src/sections/develop-detail/CustomAudioDialog";
import CustomTooltip from "../tooltip";
import SvgColor from "../svg-color";
import { buildResponseFormatMenu } from "src/utils/responseFormat";

const defaultMenus = [
  { value: "text", label: "Text" },
  { value: "json_object", label: "JSON" },
  { value: "none", label: "None" },
];

const sliderContainerStyles = {
  marginTop: "-4px",
  position: "absolute",
  left: 12,
  right: 12,
};

const selectFieldSx = {
  width: 200,
  "& .MuiInputBase-input": {
    height: "26px",
    paddingY: "2px",
  },
  "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline": {
    borderColor: "divider",
  },
};

const ReasoningProcessSwitch = ({ control, fieldName }) => {
  const { field } = useController({ control, name: fieldName });
  return (
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
        checked={field.value ?? false}
        onChange={(e) => field.onChange(e.target.checked)}
      />
    </Box>
  );
};

ReasoningProcessSwitch.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string,
};

const ModelOptionsItems = ({
  control,
  fieldNamePrefix,
  setDisabledClickOutside,
  responseSchema = [],
  items,
  responseFormat,
  booleans,
  dropdowns,
  reasoning,
  module,
  hideResponseFormat = false,
  modelConfig,
  voiceOptions,
}) => {
  const [showCreate, setShowCreate] = useState(false);
  const [isCustomAudioModalOpen, setIsCustomAudioModalOpen] = useState(false);
  const clickOutsideTimersRef = useRef([]);

  useEffect(() => {
    return () => {
      clickOutsideTimersRef.current.forEach(clearTimeout);
      clickOutsideTimersRef.current = [];
    };
  }, []);
  const { field } = useController({
    name: `${fieldNamePrefix}.responseFormat`,
    control,
  });

  const isTTSModel = modelConfig?.modelDetail?.type === "tts";

  const { field: voiceField } = useController({
    name: `${fieldNamePrefix}.voiceId`,
    control,
    defaultValue: modelConfig?.voiceId || "",
  });

  const menuItems = useMemo(() => {
    // If responseFormat exists, use it + responseSchema
    if (responseFormat?.length || module === "workbench") {
      return buildResponseFormatMenu({
        responseSchema,
        modelResponseFormat: responseFormat,
      });
    }

    // Otherwise, fallback to defaultMenus
    return [...defaultMenus];
  }, [responseSchema, responseFormat, module]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        maxHeight: "400px",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography
          variant="s3"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
        >
          Model options
        </Typography>
      </Box>
      {(Array.isArray(items) ? items : DEFAULT_MODEL_PARAMS)?.map((item) => (
        <SliderRow
          key={item?.id}
          label={item?.label}
          control={control}
          fieldName={`${fieldNamePrefix}.${item?.id}`}
          min={item?.min}
          max={item?.max}
          step={item?.step}
          sliderContainerStyles={sliderContainerStyles}
          marks={generateNMarks(item?.min, item?.max)}
          disabled={item?.disabled}
        />
      ))}
      <ShowComponent
        condition={
          !hideResponseFormat &&
          !(Array.isArray(responseFormat) && responseFormat.length === 0)
        }
      >
        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography
              variant="s3"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              Response Format
            </Typography>
          </Box>
          <FormSearchSelectFieldControl
            control={control}
            fieldName={`${fieldNamePrefix}.responseFormat`}
            onChange={() => setDisabledClickOutside(true)}
            size="small"
            placeholder="Enter Response Format"
            sx={selectFieldSx}
            options={menuItems}
            createLabel="Create new schema"
            onCreateLabel={() => {
              setShowCreate(true);
              // Delay to prevent click-outside from closing popover while create schema dialog opens
              clickOutsideTimersRef.current.push(
                setTimeout(() => setDisabledClickOutside(true), 300),
              );
            }}
            onFocus={() => setDisabledClickOutside(true)}
            onBlur={() => setDisabledClickOutside(false)}
          />
        </Box>
      </ShowComponent>
      {booleans?.map((item) => (
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
          <FormSearchSelectFieldControl
            key={item?.label}
            control={control}
            fieldName={`${fieldNamePrefix}.booleans.${item?.label}`}
            onChange={() => setDisabledClickOutside(true)}
            size="small"
            placeholder="Select"
            sx={selectFieldSx}
            options={BOOLEAN_VALUE_OPTIONS}
            onFocus={() => setDisabledClickOutside(true)}
            onBlur={() => setDisabledClickOutside(false)}
          />
        </Box>
      ))}
      {dropdowns?.map((item) => (
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
          <FormSearchSelectFieldControl
            key={item?.label}
            control={control}
            fieldName={`${fieldNamePrefix}.dropdowns.${item?.label}`}
            onChange={() => setDisabledClickOutside(true)}
            size="small"
            placeholder="Select"
            sx={selectFieldSx}
            options={item?.options?.map((op) => ({
              label: _.startCase(op),
              value: op,
            }))}
            onFocus={() => setDisabledClickOutside(true)}
            onBlur={() => setDisabledClickOutside(false)}
          />
        </Box>
      ))}
      <ShowComponent condition={isTTSModel && voiceOptions?.voices?.length > 0}>
        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography
              variant="s3"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              Voice
            </Typography>
          </Box>
          <FormSearchSelectFieldControl
            control={control}
            fieldName={`${fieldNamePrefix}.voiceId`}
            onChange={() => {
              setDisabledClickOutside(true);
            }}
            size="small"
            placeholder="Choose a voice"
            sx={{
              width: 200,
              "& .MuiInputBase-input": {
                height: "26px",
                paddingY: "2px",
              },
              "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline":
                {
                  borderColor: "whiteScale.500",
                },
            }}
            options={voiceOptions?.voices || []}
            createLabel={
              voiceOptions?.isCustomAudio ? "Add Custom Voice" : undefined
            }
            handleCreateLabel={
              voiceOptions?.isCustomAudio
                ? () => {
                    setIsCustomAudioModalOpen(true);
                    // Delay to prevent click-outside from closing popover while custom audio modal opens
                    clickOutsideTimersRef.current.push(
                      setTimeout(() => setDisabledClickOutside(true), 300),
                    );
                  }
                : undefined
            }
            onFocus={() => setDisabledClickOutside(true)}
            onBlur={() => setDisabledClickOutside(false)}
          />
        </Box>
      </ShowComponent>
      {reasoning && (
        <>
          <Divider sx={{ borderColor: "divider" }} />
          <Typography
            variant="s3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            Reasoning
          </Typography>
          {reasoning?.sliders?.map((item) => (
            <SliderRow
              key={item?.label}
              label={item?.label}
              control={control}
              fieldName={`${fieldNamePrefix}.reasoning.sliders.${_.camelCase(item?.label)}`}
              min={item?.min}
              max={item?.max}
              step={item?.step}
              marks={generateNMarks(item?.min, item?.max)}
              sliderContainerStyles={sliderContainerStyles}
              disabled={item?.disabled}
            />
          ))}
          {reasoning?.dropdowns?.map((item) => (
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
              <FormSearchSelectFieldControl
                control={control}
                fieldName={`${fieldNamePrefix}.reasoning.dropdowns.${_.camelCase(item?.label)}`}
                showClear={false}
                size="small"
                placeholder="Select"
                sx={selectFieldSx}
                options={item?.options?.map((op) => ({
                  label: _.startCase(op),
                  value: op,
                }))}
                onFocus={() => setDisabledClickOutside(true)}
                onBlur={() => setDisabledClickOutside(false)}
              />
            </Box>
          ))}
          <ReasoningProcessSwitch
            control={control}
            fieldName={`${fieldNamePrefix}.reasoning.showReasoningProcess`}
          />
        </>
      )}
      <CreateResponseSchema
        open={showCreate}
        onClose={() => {
          setShowCreate(false);
          setDisabledClickOutside(false);
        }}
        setValue={(value) => {
          field.onChange(value);
        }}
      />
      <CustomAudioDialog
        open={isCustomAudioModalOpen}
        onClose={() => {
          setIsCustomAudioModalOpen(false);
          setDisabledClickOutside(false);
        }}
        selectedModel={{
          value: modelConfig?.modelDetail?.model_name,
          providers: modelConfig?.modelDetail?.providers,
        }}
        onSuccess={(res) => {
          const customId = res?.data?.result?.id;
          if (customId) {
            voiceField.onChange(customId);
          }
        }}
      />
    </Box>
  );
};

export default ModelOptionsItems;

ModelOptionsItems.propTypes = {
  fieldNamePrefix: PropTypes.string,
  control: PropTypes.any,
  setDisabledClickOutside: PropTypes.func,
  responseSchema: PropTypes.array,
  items: PropTypes.array,
  responseFormat: PropTypes.array,
  booleans: PropTypes.array,
  dropdowns: PropTypes.array,
  reasoning: PropTypes.object,
  module: PropTypes.string,
  hideResponseFormat: PropTypes.bool,
  modelConfig: PropTypes.object,
  voiceOptions: PropTypes.object,
};
