import React from "react";
import { Box, Collapse, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import SwitchField from "src/components/Switch/SwitchField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useWatch } from "react-hook-form";
import CustomTooltip from "src/components/tooltip";

export default function CustomInstruction({ control }) {
  const theme = useTheme();
  const watchedCustomInstructionDisabled = useWatch({
    control,
    name: "customInstructionDisabled",
  });
  return (
    <Stack
      direction={"column"}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
      }}
    >
      <Stack
        direction={"row"}
        alignItems={"center"}
        justifyContent={"space-between"}
      >
        <Stack
          direction={"column"}
          gap={0}
          sx={{
            padding: theme.spacing(1.5, 2),
          }}
        >
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Use only agent definition to create scenarios
          </Typography>
          <Typography
            typography={"s2_1"}
            fontWeight={"fontWeightRegular"}
            color={"text.secondary"}
          >
            Create scenarios based on how your agent is defined
          </Typography>
        </Stack>
        <CustomTooltip
          type={"black"}
          size="small"
          show
          arrow
          title={
            <Box
              sx={{
                maxWidth: "199px",
                borderRadius: 0.5,
              }}
            >
              <Typography
                fontWeight={"fontWeightRegular"}
                typography={"s2"}
              >
                {watchedCustomInstructionDisabled
                  ? "Disable to write custom instructions for scenario creation"
                  : "Enable to auto-create scenarios using the agent definition"}
              </Typography>
            </Box>
          }
        >
          <span>
            <SwitchField
              control={control}
              fieldName="customInstructionDisabled"
              disableRipple
            />
          </span>
        </CustomTooltip>
      </Stack>
      <Collapse in={!watchedCustomInstructionDisabled}>
        <Box
          sx={{
            padding: theme.spacing(1.5, 2),
          }}
        >
          <FormTextFieldV2
            control={control}
            fieldName="customInstruction"
            label="Extra Instruction Name"
            fullWidth
            placeholder="Enter any additional instructions for the LLM to follow while generating scenarios"
            required
            size="small"
            multiline
            rows={4}
          />
        </Box>
      </Collapse>
    </Stack>
  );
}

CustomInstruction.propTypes = {
  control: PropTypes.object,
};
