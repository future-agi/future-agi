import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import ErrorMessage from "./ErrorMessage";
import { Box, DialogActions } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { z } from "zod";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

// Validation schema for array input
const verifyArray = z
  .string()
  .min(1, "Array field is required")
  .refine(
    (val) => {
      try {
        const parsed = JSON.parse(val);
        return Array.isArray(parsed) && parsed.length > 0;
      } catch {
        return false;
      }
    },
    {
      message:
        "The arrays provided are incorrect. Please verify their structure, format, and content before resubmitting.",
    },
  );

const EditArray = ({ onClose, params, onCellValueChanged }) => {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const { control, reset, setValue } = useForm({
    defaultValues: { name: "" },
  });

  useEffect(() => {
    if (params?.value) {
      setName(params?.value);
      setValue("name", params?.value);
    }
  }, [params?.value, setValue]);

  const handleClose = () => {
    onClose();
    reset();
  };

  const onSubmit = (e) => {
    e.preventDefault();
    try {
      // Validate the value as a valid array
      verifyArray.parse(name); // This will throw if validation fails
      onCellValueChanged({ ...params, newValue: name });
      handleClose();
    } catch (err) {
      setError(
        err?.errors?.[0]?.message ||
          err?.message ||
          "An error occurred while saving the array",
      ); // Set the validation error message
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
          control={control}
          onChange={(e) => setName(e.target.value)}
          placeholder="Enter name"
          fieldName="name"
          hidelabel
          fullWidth
          multiline
          rows={4}
          sx={{
            backgroundColor: "var(--bg-input)",
            borderRadius: "8px",
            "& .MuiOutlinedInput-notchedOutline": {
              border: "none", // Removes the border
            },
          }}
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
            fontsize: "14px",
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

EditArray.propTypes = {
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};

export default EditArray;
