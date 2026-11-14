import { LoadingButton } from "@mui/lab";
import { Box, DialogActions, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import ErrorMessage from "./ErrorMessage";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

// Integer validation schema
const verifyInteger = z.preprocess(
  (val) => {
    if (val === "" || val === undefined || val === null) return undefined; // Prevent empty input
    const parsedValue = Number(val);
    return isNaN(parsedValue) ? undefined : parsedValue;
  },
  z
    .number({ required_error: "Number is required." })
    .min(0, "At least one digit required")
    .int({
      message:
        "The number provided is not an integer. Please verify and provide a valid integer value.",
    }),
);

// Float validation schema
const verifyFloat = z.preprocess(
  (val) => {
    if (val === "" || val === undefined || val === null) return undefined; // Prevent empty input
    const parsedValue = Number(val);
    return isNaN(parsedValue) ? undefined : parsedValue; // Accepts both integers and floats
  },
  z
    .number({ required_error: "Number is required." })
    .min(0, "At least one digit required"),
);

const EditFlotInteger = ({
  fieldType,
  params,
  onClose,
  onCellValueChanged,
}) => {
  const theme = useTheme();
  const [number, setNumber] = useState("");
  const [error, setError] = useState("");
  const { control, reset, setValue } = useForm({
    defaultValues: { number: "" },
  });

  // Set initial value from params
  useEffect(() => {
    if (
      params?.value !== null &&
      params?.value !== undefined &&
      !isNaN(params?.value)
    ) {
      setNumber(params?.value);
      setValue("number", params?.value);
    }
  }, [params?.value, setValue]);

  const handleClose = () => {
    onClose();
    reset();
    setError(""); // Clear error on close
  };

  // Validate the input value based on the type
  const validateValue = (value) => {
    const isFloat = value?.toString()?.includes(".");
    try {
      if (fieldType === "Float") {
        verifyFloat?.parse(value);
      } else if (fieldType === "Integer") {
        verifyInteger.parse(value); // Validate as integer
        if (isFloat)
          throw new Error(
            "The number provided is not an integer. Please verify and provide a valid integer value.",
          );
      }
      return true;
    } catch (err) {
      const errorMessage =
        err?.errors?.[0]?.message || err?.message || "Invalid value.";
      setError(errorMessage);
      return false;
    }
  };

  const onSubmit = (e) => {
    e.preventDefault();
    setError(""); // Clear previous errors

    if (validateValue(number)) {
      onCellValueChanged({ ...params, newValue: number });
      handleClose();
    }
  };

  return (
    <Box
      sx={{
        paddingX: "20px",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        marginTop: "-2px",
        width: "421px",
      }}
      component="form"
      onSubmit={onSubmit}
    >
      <Box>
        <FormTextFieldV2
          type="number"
          control={control}
          onChange={(e) => setNumber(e.target.value)}
          placeholder={`Enter ${fieldType} Number`}
          fieldName="number"
          hidelabel
          fullWidth
          sx={{
            borderRadius: "8px",
            background: theme.palette.background.neutral,
            "& .MuiOutlinedInput-notchedOutline": {
              border: "none",
              height: "46px",
            },
          }}
          showError={false}
        />
        {error && (
          <ErrorMessage isError={Boolean(error)} errorMessage={error} />
        )}
      </Box>
      <DialogActions
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          marginRight: "-20px",
          marginTop: "-15px",
        }}
      >
        <LoadingButton
          variant="outlined"
          size="small"
          onClick={handleClose}
          sx={{
            width: "90px",
            marginRight: "-5px",
          }}
        >
          Cancel
        </LoadingButton>
        <LoadingButton
          variant="contained"
          color="primary"
          type="submit"
          size="small"
          loading={false}
          sx={{ width: "90px" }}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </Box>
  );
};

EditFlotInteger.propTypes = {
  params: PropTypes.object,
  onClose: PropTypes.func,
  fieldType: PropTypes.string,
  onCellValueChanged: PropTypes.func,
};

export default EditFlotInteger;
