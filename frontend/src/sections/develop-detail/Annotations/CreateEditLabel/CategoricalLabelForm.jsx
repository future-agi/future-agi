import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import ChoicesInput from "src/components/ChoiceInput/ChoicesInput";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import HelperText from "../../Common/HelperText";

const CategoricalLabelForm = ({ control }) => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: theme.palette.divider,
        py: theme.spacing(2),
        px: theme.spacing(1),
        borderRadius: theme.spacing(1),
      }}
    >
      <ChoicesInput
        control={control}
        config={control}
        configKey="settings.options"
        fieldPrefix=""
        label={
          <Typography
            fontSize={13}
            fontWeight={500}
            sx={{ mb: theme.spacing(1) }}
          >
            Choices
            <Typography component="span" color={theme.palette.status.danger.fg}>
              *
            </Typography>
          </Typography>
        }
        helperText="Create a list of predefined options or categories. Used when multi_choice is true."
        fieldLabel="Choice"
      />
      <FormCheckboxField
        control={control}
        fieldName="settings.multiChoice"
        helperText={
          <HelperText
            text="Whether the output is a multiple choice question or not"
            sx={{ fontSize: "12px" }}
          />
        }
        labelPlacement="start"
        label={
          <Typography fontWeight={600} fontSize={13}>
            Multi Choice
            <Typography component="span" color={theme.palette.status.danger.fg}>
              *
            </Typography>
          </Typography>
        }
      />
    </Box>
  );
};

CategoricalLabelForm.propTypes = {
  control: PropTypes.any,
};

export default CategoricalLabelForm;
