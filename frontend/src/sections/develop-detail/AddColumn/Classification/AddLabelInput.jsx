import { Box, Button, FormHelperText, IconButton } from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React from "react";
import { useFieldArray, useFormState } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";

const AddLabelRow = ({ control, index, remove, field, required = false }) => {
  return (
    <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
      <FormTextFieldV2
        fullWidth
        required={required}
        fieldName={`${field}.${index}.value`}
        control={control}
        placeholder="Enter label"
        label="Label"
        size="small"
        autoFocus
      />
      <IconButton size="small" onClick={remove}>
        <Iconify
          icon="solar:trash-bin-trash-bold"
          sx={{ color: "text.secondary" }}
        />
      </IconButton>
    </Box>
  );
};

AddLabelRow.propTypes = {
  control: PropTypes.object,
  index: PropTypes.number,
  remove: PropTypes.func,
  field: PropTypes.string,
  required: PropTypes.bool,
};

const AddLabelInput = ({ control, field, ...rest }) => {
  const { fields, append, remove } = useFieldArray({
    control,
    name: field,
  });

  const { errors } = useFormState({ control });

  const errorMessage = _.get(errors, `${field}.message`);

  return (
    <Box
      sx={{
        paddingX: 2,
        paddingBottom: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
      }}
    >
      {fields.map((f, idx) => (
        <AddLabelRow
          key={f.id}
          control={control}
          index={idx}
          remove={remove}
          field={field}
          {...rest}
        />
      ))}
      <Box>
        <Button
          color="primary"
          startIcon={<Iconify icon="solar:add-linear" />}
          size="small"
          onClick={() => append({ id: getRandomId(), value: "" })}
        >
          Add Label
        </Button>
      </Box>
      {errorMessage?.length && (
        <FormHelperText error sx={{ margin: 0 }}>
          {errorMessage}
        </FormHelperText>
      )}
    </Box>
  );
};

AddLabelInput.propTypes = {
  control: PropTypes.object,
  field: PropTypes.string,
};

export default AddLabelInput;
