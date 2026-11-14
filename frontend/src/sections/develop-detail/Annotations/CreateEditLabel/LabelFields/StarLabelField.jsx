import { Box, Rating, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useController } from "react-hook-form";
import Iconify from "src/components/iconify";

const ControlledRating = ({ control, fieldName, settings }) => {
  const { field } = useController({ name: fieldName, control });

  return (
    <Rating
      name="simple-controlled"
      value={field.value}
      onChange={(event, newValue) => {
        field.onChange(newValue);
      }}
      icon={
        <Iconify
          icon="solar:star-bold"
          sx={{ fontSize: "inherit" }}
          width={25}
        />
      }
      emptyIcon={
        <Iconify
          icon="solar:star-linear"
          width={25}
          sx={{ fontSize: "inherit" }}
        />
      }
      max={parseInt(settings.noOfStars) || 5}
    />
  );
};

ControlledRating.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
  settings: PropTypes.object,
};

const StarLabelField = ({ control, label, fieldName, settings }) => {
  const [value, setValue] = useState(0);
  const theme = useTheme();
  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        gap: theme.spacing(1),
        flexDirection: "column",
        my: 1,
      }}
    >
      {!control && (
        <Typography variant="s1" fontWeight={500}>
          {label || "Test Label:"}
        </Typography>
      )}
      {control ? (
        <>
          <ControlledRating
            control={control}
            fieldName={fieldName}
            settings={settings}
          />
        </>
      ) : (
        <Rating
          name="simple-controlled"
          value={value}
          onChange={(event, newValue) => {
            setValue(newValue);
          }}
          icon={
            <Iconify
              icon="solar:star-bold"
              sx={{ fontSize: "inherit" }}
              width={25}
            />
          }
          emptyIcon={
            <Iconify
              icon="solar:star-linear"
              width={25}
              sx={{ fontSize: "inherit" }}
            />
          }
          max={
            parseInt(settings.noOfStars) >= 0 ? parseInt(settings.noOfStars) : 5
          }
        />
      )}
    </Box>
  );
};

StarLabelField.propTypes = {
  control: PropTypes.object,
  label: PropTypes.string,
  fieldName: PropTypes.string,
  settings: PropTypes.object,
};

export default StarLabelField;
