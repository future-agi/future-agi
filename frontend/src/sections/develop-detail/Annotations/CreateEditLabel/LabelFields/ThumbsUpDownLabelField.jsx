import { Box, IconButton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { Controller } from "react-hook-form";
import Iconify from "src/components/iconify";

const ThumbsUpDown = ({ value, setValue }) => {
  const theme = useTheme();
  return (
    <Box
      sx={{ display: "flex", gap: theme.spacing(1.5), alignItems: "center" }}
    >
      <IconButton onClick={() => setValue("up")} sx={{ p: theme.spacing(0) }}>
        <Iconify
          icon="solar:like-linear"
          color={
            value === "up"
              ? theme.palette.status.success.fg
              : theme.palette.text.secondary
          }
        />
      </IconButton>
      <IconButton onClick={() => setValue("down")} sx={{ p: theme.spacing(0) }}>
        <Iconify
          icon="solar:dislike-linear"
          color={
            value === "down"
              ? theme.palette.status.danger.fg
              : theme.palette.text.secondary
          }
        />
      </IconButton>
    </Box>
  );
};

const ControlledThumbsUpDown = ({ control, fieldName }) => {
  return (
    <>
      <Controller
        control={control}
        name={fieldName}
        render={({ field }) => (
          <ThumbsUpDown
            value={field.value}
            setValue={(v) => field.onChange(v)}
          />
        )}
      />
    </>
  );
};

ControlledThumbsUpDown.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
};

ThumbsUpDown.propTypes = {
  value: PropTypes.string,
  setValue: PropTypes.func,
};

const ThumbsUpDownLabelField = ({ control, label, fieldName }) => {
  const [value, setValue] = useState("up");
  const theme = useTheme();

  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        gap: theme.spacing(1.5),
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
        <ControlledThumbsUpDown control={control} fieldName={fieldName} />
      ) : (
        <ThumbsUpDown value={value} setValue={setValue} />
      )}
    </Box>
  );
};

ThumbsUpDownLabelField.propTypes = {
  control: PropTypes.object,
  label: PropTypes.string,
  fieldName: PropTypes.string,
  settings: PropTypes.object,
};

export default ThumbsUpDownLabelField;
