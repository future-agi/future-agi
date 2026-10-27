import { Box, Typography } from "@mui/material";
import React from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { LanguageOptions } from "./common";
import LanguageMultiSelect from "./LanguageMultiselect";
import SwitchField from "src/components/Switch/SwitchField";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";

const LanguageSelector = ({ multiple = true }) => {
  const { control, setValue } = useFormContext();

  const isMultilingual = useWatch({ control, name: "multilingual" });

  if (!multiple) {
    return (
      <FormSearchSelectFieldControl
        control={control}
        fieldName="language"
        label="Select language"
        placeholder="Select language"
        size="small"
        required
        fullWidth
        options={LanguageOptions}
        sx={{ backgroundColor: "background.paper" }}
        InputLabelProps={{
          sx: {
            background: "transparent !important",
          },
        }}
      />
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        borderRadius: "4px",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box>
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Multilingual
          </Typography>
          <Typography
            typography="s1"
            fontWeight="fontWeightRegular"
            color="text.secondary"
          >
            Enable your persona to communicate in multiple languages during
            calls
          </Typography>
        </Box>

        <SwitchField
          control={control}
          fieldName="multilingual"
          onChange={(e) => {
            if (!e.target.checked) {
              // multilingual turned off
              setValue("language", "");
            }
          }}
        />
      </Box>
      <Box>
        <ShowComponent condition={isMultilingual}>
          <LanguageMultiSelect
            control={control}
            fieldName="language"
            label="Select language"
            placeholder="Select language"
            size="small"
            required
            options={LanguageOptions.map((option) => ({
              label: option.label,
              id: option.value,
            }))}
            fullWidth
            dropDownMaxHeight={250}
          />
        </ShowComponent>
        <ShowComponent condition={!isMultilingual}>
          <FormSearchSelectFieldControl
            control={control}
            fieldName="language"
            label="Select language"
            placeholder="Select language"
            size="small"
            required
            fullWidth
            options={LanguageOptions}
            sx={{ backgroundColor: "background.paper" }}
            InputLabelProps={{
              sx: {
                background: "transparent !important",
              },
            }}
          />
        </ShowComponent>
      </Box>
    </Box>
  );
};

LanguageSelector.propTypes = {
  multiple: PropTypes.bool,
};

export default LanguageSelector;
