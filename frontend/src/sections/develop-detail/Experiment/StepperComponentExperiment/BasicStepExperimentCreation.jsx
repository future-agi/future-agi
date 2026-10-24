import { Box, Typography, CircularProgress } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import RadioField from "src/components/RadioField/RadioField";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import StepsHeaderComponent from "./StepsHeaderComponent";
import { experimentTypeOptions } from "../common";

const CustomRadioButtonForModelType = ({
  option,
  isSelected,
  onChange,
  error,
}) => {
  return (
    <Box
      onClick={() => {
        onChange(option.value);
      }}
      sx={{
        border: isSelected ? "1px solid" : "1px solid",
        borderRadius: "4px",
        padding: 1.5,
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        width: "160px",
        boxSizing: "border-box",
        borderColor: error
          ? "status.danger.fg"
          : isSelected
            ? "text.primary"
            : "border.light",

        marginRight: 1.25,
        gap: 1.25,
        backgroundColor: isSelected
          ? "status.discovery.bg"
          : "background.paper",
      }}
    >
      <Box
        sx={{
          height: 36,
          width: 36,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: isSelected
            ? "status.discovery.bg"
            : "background.default",
          padding: "6px",
          borderRadius: "4px",
        }}
      >
        <SvgColor
          width={24}
          sx={{ color: isSelected ? "primary.main" : "text.primary" }}
          src={option.icon}
        />
      </Box>

      <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
        {option.label}
      </Typography>
      <Typography
        typography={"s2"}
        color="text.subtitle"
        sx={{
          whiteSpace: "normal",
          overflow: "visible",
        }}
      >
        {option.subtitle}
      </Typography>
    </Box>
  );
};

CustomRadioButtonForModelType.propTypes = {
  option: PropTypes.shape({
    value: PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.number,
      PropTypes.bool,
    ]),
    icon: PropTypes.string,
    label: PropTypes.string,
    subtitle: PropTypes.string,
  }).isRequired,
  isSelected: PropTypes.bool.isRequired,
  onChange: PropTypes.func.isRequired,
  error: PropTypes.string,
};

const BasicStepExperimentCreation = ({
  control,
  title,
  subTitle,
  handleModelTypeChange,
  isValidatingName = false,
  isNameValid = false,
}) => {
  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 2, height: "100%" }}
    >
      <StepsHeaderComponent
        title={"Basic information"}
        subtitle={"Add basic info and models to experiment with"}
      />

      <Box sx={{ position: "relative" }}>
        <FormTextFieldV2
          control={control}
          fieldName="name"
          label="Experiment Name"
          size="small"
          fullWidth
          placeholder="Experiment Name"
          helperText={undefined}
          defaultValue={undefined}
          onBlur={undefined}
          sx={{
            "& .MuiOutlinedInput-root": {
              paddingRight: "40px",
            },
          }}
        />
        <Box
          sx={{
            position: "absolute",
            right: 12,
            top: "50%",
            transform: "translateY(-50%)",
            display: "flex",
            alignItems: "center",
            pointerEvents: "none",
          }}
        >
          {isValidatingName && (
            <CircularProgress size={16} sx={{ color: "text.disabled" }} />
          )}
          {!isValidatingName && isNameValid && (
            <Iconify
              icon="solar:check-circle-bold"
              sx={{ color: "success.main", width: 16, height: 16 }}
            />
          )}
        </Box>
      </Box>

      <Box>
        <Typography
          sx={{ display: "flex", flexDirection: "row", alignItems: "center" }}
          typography="s1_2"
          fontWeight={"fontWeightMedium"}
        >
          {title}
          <Typography type="span" sx={{ color: "status.danger.fg" }}>
            *
          </Typography>
        </Typography>
        <Typography
          typography={"s2_1"}
          fontWeight={"fontWeightRegular"}
          color="text.secondary"
        >
          {subTitle}
        </Typography>
      </Box>

      <RadioField
        parentSx={{
          marginLeft: -1,
          marginTop: -1.5,
        }}
        groupSx={{
          flexWrap: "wrap",
          gap: 0,
        }}
        helperTextSx={{
          marginLeft: 1,
          marginTop: 2,
        }}
        optionDirection="row"
        renderCustomOption={CustomRadioButtonForModelType}
        control={control}
        fieldName={"experimentType"}
        onChange={handleModelTypeChange}
        options={experimentTypeOptions}
      />
    </Box>
  );
};

BasicStepExperimentCreation.propTypes = {
  control: PropTypes.object,
  handleModelTypeChange: PropTypes.func,
  title: PropTypes.string,
  subTitle: PropTypes.string,
  isValidatingName: PropTypes.bool,
  isNameValid: PropTypes.bool,
};

export default BasicStepExperimentCreation;
