import { Box, Button, IconButton } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useFieldArray, useWatch } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import SecretSelect from "src/sections/common/SecretSelect/SecretSelect";
import RequestBody from "./RequestBody";
import { getRandomId } from "src/utils/utils";

const ValueSelector = ({ control, field, type, allColumns, jsonSchemas }) => {
  if (type === "PlainText") {
    return (
      <FormTextFieldV2
        control={control}
        fieldName={field}
        size="small"
        label="Value"
        placeholder="Enter value"
        fullWidth
      />
    );
  }

  if (type === "Variable") {
    return (
      <RequestBody
        control={control}
        contentFieldName={field}
        allColumns={allColumns}
        jsonSchemas={jsonSchemas}
        placeholder="{{variable}}"
        multiline={false}
        showHelper={false}
      />
    );
  }

  if (type === "Secret") {
    return (
      <SecretSelect
        control={control}
        fieldName={field}
        size="small"
        label="Value"
        fullWidth
      />
    );
  }

  return <Box></Box>;
};

ValueSelector.propTypes = {
  control: PropTypes.object,
  field: PropTypes.string,
  type: PropTypes.string,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
};

const AddFieldInputRow = ({
  control,
  fieldPrefix,
  index,
  onRemove,
  allColumns,
  jsonSchemas,
}) => {
  const type = useWatch({ control, name: `${fieldPrefix}.${index}.type` });

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <FormTextFieldV2
        control={control}
        fieldName={`${fieldPrefix}.${index}.name`}
        size="small"
        label="Key"
        placeholder="Enter key"
        fullWidth
      />
      <FormSelectField
        control={control}
        fieldName={`${fieldPrefix}.${index}.type`}
        size="small"
        label="Type"
        options={[
          { label: "Plain Text", value: "PlainText" },
          { label: "Secret", value: "Secret" },
          { label: "Variable", value: "Variable" },
        ]}
        fullWidth
      />
      <ValueSelector
        control={control}
        field={`${fieldPrefix}.${index}.value`}
        type={type}
        allColumns={allColumns}
        jsonSchemas={jsonSchemas}
      />
      <IconButton size="small" onClick={() => onRemove(index)}>
        <Iconify
          icon="solar:trash-bin-trash-bold"
          sx={{ color: "text.secondary" }}
        />
      </IconButton>
    </Box>
  );
};

AddFieldInputRow.propTypes = {
  control: PropTypes.object,
  fieldPrefix: PropTypes.string,
  config: PropTypes.object,
  index: PropTypes.number,
  onRemove: PropTypes.func,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
};

const AddFieldInput = ({ control, fieldName, allColumns, jsonSchemas }) => {
  const { fields, append, remove } = useFieldArray({
    control,
    name: fieldName,
  });

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
        <AddFieldInputRow
          key={f.id}
          config={f}
          fieldPrefix={fieldName}
          index={idx}
          control={control}
          onRemove={remove}
          allColumns={allColumns}
          jsonSchemas={jsonSchemas}
        />
      ))}
      <Box>
        <Button
          startIcon={<Iconify icon="mdi:plus" />}
          color="primary"
          size="small"
          sx={{ fontWeight: 400 }}
          onClick={() =>
            append({
              id: getRandomId(),
              name: "",
              type: "PlainText",
              value: "",
            })
          }
        >
          Add Field
        </Button>
      </Box>
    </Box>
  );
};

AddFieldInput.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
  allColumns: PropTypes.array,
  jsonSchemas: PropTypes.object,
};

export default AddFieldInput;
