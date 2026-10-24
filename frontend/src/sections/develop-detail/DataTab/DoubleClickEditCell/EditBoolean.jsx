import { Box, DialogActions } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import ErrorMessage from "./ErrorMessage";
import { LoadingButton } from "@mui/lab";
import RadioField from "src/components/RadioField/RadioField";

const EditBoolean = ({ params, onClose, onCellValueChanged }) => {
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
      handleClose();
    } catch (err) {
      setError(err?.errors[0]?.message);
    }
  };

  return (
    <Box
      sx={{
        marginTop: "-20px",
        marginLeft: "-20px",
        padding: "0px 20px 20px",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        height: "130px",
        width: "421px",
      }}
      component="form"
      onSubmit={onSubmit}
    >
      <Box>
        <RadioField
          control={control}
          onChange={(e) => setName(e.target.value)}
          fieldName="name"
          options={[
            { label: "True", value: "true" },
            { label: "False", value: "false" },
          ]}
        />
        {error && (
          <ErrorMessage isError={Boolean(error)} errorMessage={error} />
        )}
      </Box>
      <DialogActions
        sx={{ display: "flex", justifyContent: "flex-end", padding: "0px" }}
      >
        <LoadingButton
          size="small"
          onClick={handleClose}
          sx={{
            width: "90px",
            backgroundColor: "action.selected",
            color: "var(--text-primary)",
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
          sx={{
            width: "90px",
            color: "primary.contrastText",
          }}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </Box>
  );
};

export default EditBoolean;

EditBoolean.propTypes = {
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};
