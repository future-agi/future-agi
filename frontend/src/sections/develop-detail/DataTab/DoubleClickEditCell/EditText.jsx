import { LoadingButton } from "@mui/lab";
import { Box, DialogActions } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import ErrorMessage from "./ErrorMessage";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const EditText = ({ params, onClose, onCellValueChanged }) => {
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
      onCellValueChanged({ ...params, newValue: name });
      // gridApiRef?.current?.api?.refreshServerSide();
      handleClose();
    } catch (err) {
      setError(err?.errors[0]?.message || "An error occur while Save Text");
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
          // fullWidth
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

export default EditText;

EditText.propTypes = {
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};
