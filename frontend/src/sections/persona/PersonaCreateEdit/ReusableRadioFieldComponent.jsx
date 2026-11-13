import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import RadioField from "src/components/RadioField/RadioField";

const ReusableRadioFieldComponent = ({
  control,
  options,
  title,
  fieldName,
}) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
      }}
    >
      <Typography variant="s1" fontWeight={"fontWeightMedium"}>
        {title}
      </Typography>
      <RadioField
        control={control}
        fieldName={fieldName}
        options={options}
        optionDirection="row"
        custom
        hideRadioField
        parentSx={{
          marginLeft: -2,
        }}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          height: "34px",
          py: 0.75,
          px: 1.75,
        }}
        getOptionSx={(option, isSelected) => ({
          border: "1px solid",
          borderColor: isSelected ? "primary.main" : "divider",
          backgroundColor: isSelected ? "action.hover" : "background.paper",
        })}
        labelTxtsx={{
          fontWeight: 400,
          fontSize: "13px",
        }}
      />
    </Box>
  );
};

export default ReusableRadioFieldComponent;

ReusableRadioFieldComponent.propTypes = {
  title: PropTypes.string,
  options: PropTypes.array,
  control: PropTypes.func,
  fieldName: PropTypes.string,
};
