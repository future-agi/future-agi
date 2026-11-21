import { Box, Typography } from "@mui/material";
import React from "react";
import {
  CustomPersonaAccordion,
  CustomPersonaAccordionContent,
  CustomPersonaAccordionHeader,
} from "./PersonCustomComponents";
import SvgColor from "src/components/svg-color";
import {
  AccentOptions,
  CommunicationStyleOptions,
  PersonalityOptions,
} from "./common";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { useFormContext } from "react-hook-form";
import OptionSelectors from "./OptionSelectors";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { AGENT_TYPES } from "src/sections/agents/constants";

const PersonaBehavioralSetting = ({
  multiple = true,
  showClearButton = false,
  type = AGENT_TYPES.VOICE,
}) => {
  const {
    control,
    formState: { errors },
  } = useFormContext();

  const personalityError = errors?.personality?.message;

  return (
    <Box>
      <CustomPersonaAccordion disableGutters defaultExpanded>
        <CustomPersonaAccordionHeader
          expandIcon={
            <SvgColor src="/assets/icons/custom/lucide--chevron-down.svg" />
          }
        >
          Behavioural Settings
        </CustomPersonaAccordionHeader>
        <CustomPersonaAccordionContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: "24px" }}>
            <Box>
              <OptionSelectors
                label="Select Personality"
                description="Define your persona’s personality type for realistic simulations."
                fieldName="personality"
                options={PersonalityOptions}
                multiple={multiple}
                showClearButton={showClearButton}
              />
              {personalityError ? (
                <Typography
                  variant="body2"
                  color="error"
                  role="alert"
                  sx={{ mt: 0.5 }}
                >
                  {personalityError}
                </Typography>
              ) : null}
            </Box>
            <FormSearchSelectFieldControl
              control={control}
              fieldName="communicationStyle"
              label="Select communication style"
              fullWidth
              placeholder="Direct and concise"
              size="small"
              options={CommunicationStyleOptions}
              multiple={multiple}
              checkbox
              selectAll
            />
            <ShowComponent condition={type === AGENT_TYPES.VOICE}>
              <FormSearchSelectFieldControl
                control={control}
                fieldName="accent"
                label="Accent"
                fullWidth
                placeholder="American"
                size="small"
                options={AccentOptions}
                multiple={multiple}
                checkbox
                selectAll
              />
            </ShowComponent>
          </Box>
        </CustomPersonaAccordionContent>
      </CustomPersonaAccordion>
    </Box>
  );
};

PersonaBehavioralSetting.propTypes = {
  multiple: PropTypes.bool,
  showClearButton: PropTypes.bool,
  type: PropTypes.string,
};

export default PersonaBehavioralSetting;
