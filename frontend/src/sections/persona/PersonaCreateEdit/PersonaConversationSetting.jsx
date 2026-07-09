import { Box, Typography, useTheme } from "@mui/material";
import React from "react";
import {
  CustomPersonaAccordion,
  CustomPersonaAccordionContent,
  CustomPersonaAccordionHeader,
} from "./PersonCustomComponents";
import SvgColor from "src/components/svg-color";
import LanguageSelector from "./LanguageSelector";
import OptionSelectors from "./OptionSelectors";
import { ConversationSpeedOptions } from "./common";
import RadioField from "src/components/RadioField/RadioField";
import { useFormContext } from "react-hook-form";
import SliderRow from "src/sections/common/SliderRow/SliderRow";
import { generateNMarks } from "src/sections/develop-detail/Common/common";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";

const PersonaConversationSetting = ({
  multiple = true,
  viewOptions,
  showClearButton = false,
}) => {
  const { control } = useFormContext();
  const showBackgroundSound =
    viewOptions?.backgroundSound !== undefined
      ? viewOptions?.backgroundSound
      : true;
  const theme = useTheme();
  const sliderMarkStyles = {
    "& .MuiSlider-mark": {
      backgroundColor: "action.selected",
      height: theme.spacing(0.5),
      width: theme.spacing(0.25),
    },
    "& .MuiSlider-thumb": {
      zIndex: 3,
      width: theme.spacing(1.5),
      height: theme.spacing(1.5),
      backgroundColor: theme.palette.text.disabled,
      border: `2px solid`,
      borderColor: theme.palette.text.disabled,
    },
    "& .MuiSlider-rail": {
      backgroundColor: `${theme.palette.action.selected} !important`,
    },
  };

  return (
    <Box>
      <CustomPersonaAccordion disableGutters defaultExpanded>
        <CustomPersonaAccordionHeader
          expandIcon={
            <SvgColor src="/assets/icons/custom/lucide--chevron-down.svg" />
          }
        >
          Conversation Settings
        </CustomPersonaAccordionHeader>
        <CustomPersonaAccordionContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: "24px" }}>
            <LanguageSelector multiple={multiple} />
            <OptionSelectors
              label="Conversation Speed"
              description="Adjust how fast or slow your persona speaks"
              fieldName="conversationSpeed"
              options={ConversationSpeedOptions}
              multiple={multiple}
              showClearButton={showClearButton}
            />
            <ShowComponent condition={showBackgroundSound}>
              <RadioField
                control={control}
                fieldName="backgroundSound"
                options={[
                  { label: "Yes", value: "true" },
                  { label: "No", value: "false" },
                ]}
                optionDirection="row"
                customLabel={
                  <Box>
                    <Typography variant="s1_2" fontWeight="fontWeightMedium">
                      Background noise
                    </Typography>
                    <Typography
                      typography="s1"
                      fontWeight="fontWeightRegular"
                      color="text.secondary"
                    >
                      To test how your agent interprets and responds in
                      real-world conditions
                    </Typography>
                  </Box>
                }
                groupSx={{ paddingX: 0 }}
                optionColor="text.primary"
              />
            </ShowComponent>
            <SliderRow
              label="Finished Speaking Sensitivity"
              description="Control how quickly the persona starts talking after the agent pauses"
              control={control}
              fieldName="finishedSpeakingSensitivity"
              min={1}
              max={10}
              step={1}
              marks={generateNMarks(1, 10)}
              sx={sliderMarkStyles}
              labelProps={{ variant: "s1_2", fontWeight: "fontWeightMedium" }}
              showClearButton={showClearButton}
              minDescription="Waits for longer pauses"
              maxDescription="Jumps in after short pauses"
            />
            <SliderRow
              label="Interrupt Sensitivity"
              description="Control how easily the persona stops talking when agent starts speaking"
              control={control}
              fieldName="interruptSensitivity"
              min={1}
              max={10}
              step={1}
              marks={generateNMarks(1, 10)}
              sx={sliderMarkStyles}
              labelProps={{ variant: "s1_2", fontWeight: "fontWeightMedium" }}
              showClearButton={showClearButton}
              minDescription="Does not respond to interruptions"
              maxDescription="Stops as soon as interrupted"
            />
            {/* <OptionSelectors
              label="Speech Clarity"
              fieldName="conversationSpeed"
              options={ConversationSpeedOptions}
            /> */}
          </Box>
        </CustomPersonaAccordionContent>
      </CustomPersonaAccordion>
    </Box>
  );
};

PersonaConversationSetting.propTypes = {
  multiple: PropTypes.bool,
  viewOptions: PropTypes.shape({
    backgroundSound: PropTypes.bool,
  }),
  showClearButton: PropTypes.bool,
};

export default PersonaConversationSetting;
