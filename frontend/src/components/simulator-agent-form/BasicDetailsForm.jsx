import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "../FromSearchSelectField";
import HeadingAndSubheading from "../HeadingAndSubheading/HeadingAndSubheading";
import ConfigureKeysModal from "../ConfigureApiKeysModal/ConfigureKeysModal";
import HeadingAndSubHeading from "../HeadingAndSubheading/HeadingAndSubheading";
import CustomModelDropdownControl from "../custom-model-dropdown/CustomModelDropdownControl";
import SliderRow from "../custom-model-options/SliderRow/SliderRow";
import { generateNMarks } from "src/sections/develop-detail/Common/common";

const BasicDetailsForm = ({ control, errors }) => {
  const [isApiConfigurationOpen, setIsApiConfigurationOpen] = useState(null);
  return (
    <Box py={3} display={"flex"} flexDirection={"column"} gap={2}>
      <Box display={"flex"} flexDirection={"row"} gap={2}>
        <FormTextFieldV2
          control={control}
          placeholder={"Enter agent name"}
          label={
            <Typography typography={"s1"} fontWeight={600}>
              Agent Name
            </Typography>
          }
          fieldName={"name"}
          fullWidth
          required
          size={"small"}
          helperText={""}
          error={errors.name}
        />
        <FormSearchSelectFieldControl
          control={control}
          defaultValue="Voice"
          options={[
            {
              label: "Voice",
              value: "Voice",
            },
            {
              label: "Chat",
              value: "Chat",
              disabled: true,
              component: (
                <Box px={1}>
                  <HeadingAndSubheading
                    heading={
                      <Typography typography={"s1"}>{"Chat"}</Typography>
                    }
                    subHeading="Upgrade to enterprise to use this"
                  ></HeadingAndSubheading>
                </Box>
              ),
            },
          ]}
          error={errors?.model && errors.model.message}
          fieldName={"agentType"}
          label={"Agent type"}
          size={"small"}
          fullWidth
          required
        />
      </Box>
      <FormTextFieldV2
        control={control}
        placeholder={"Enter prompt"}
        fieldName={"prompt"}
        label={
          <Typography typography={"s1"} fontWeight={600}>
            Prompt
          </Typography>
        }
        multiline
        required
        rows={12}
        maxRows={12}
      />
      <HeadingAndSubHeading
        heading={
          <Box
            sx={{
              width: "100%",
            }}
          >
            <ConfigureKeysModal
              open={Boolean(isApiConfigurationOpen)}
              selectedModel={isApiConfigurationOpen}
              onClose={() => setIsApiConfigurationOpen(null)}
            />
            <CustomModelDropdownControl
              control={control}
              fieldName="model"
              label="Language Model"
              searchDropdown
              modelObjectKey={null}
              size="small"
              fullWidth
              excludeCustomProviders
              onModelConfigOpen={(selectedModel) => {
                setIsApiConfigurationOpen(selectedModel);
              }}
              required
              inputSx={{
                "&.MuiInputLabel-root, .MuiInputLabel-shrink": {
                  fontWeight: "fontWeightMedium",
                  color: "text.disabled",
                },
                "&.Mui-focused.MuiInputLabel-shrink": {
                  color: "text.disabled",
                },
                "& .MuiInputLabel-root.Mui-focused": {
                  color: "text.secondary",
                },
              }}
              showIcon
              fieldPrefix={undefined}
            />
          </Box>
        }
      />
      <Box px={1}>
        <SliderRow
          label="LLM Temperature"
          control={control}
          fieldName={"llmTemperature"}
          min={0}
          max={1}
          step={0.1}
          sliderContainerStyles={{}}
          marks={generateNMarks(0, 1)}
          inputSectionStyles={{
            "& .slider-label": {
              fontSize: "13px",
            },
          }}
        />
      </Box>
    </Box>
  );
};

export default BasicDetailsForm;

BasicDetailsForm.propTypes = {
  control: PropTypes.object,
  errors: PropTypes.object,
};
