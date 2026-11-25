import { Box, FormHelperText, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { camelCaseToTitleCase, getRandomId } from "src/utils/utils";
import HelperText from "../HelperText";
import Iconify from "src/components/iconify";
import { useFieldArray, useFormState } from "react-hook-form";
import { FormSelectField } from "src/components/FormSelectField";
import _ from "lodash";

const RuleStringInput = ({ control, config, configKey, allColumns }) => {
  const fieldName = `config.config.${configKey}`;
  const helperText =
    config?.configParamsDesc?.[configKey] ||
    config?.config_params_desc?.[configKey];

  const { fields, append, remove } = useFieldArray({
    control,
    name: fieldName,
  });

  const { errors } = useFormState({ control });

  const errorMessage =
    _.get(errors, fieldName)?.root?.message ||
    _.get(errors, fieldName)?.message ||
    "";
  const isError = !!errorMessage;

  return (
    <Box sx={{ display: "flex", gap: 1, flexDirection: "column" }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <Box sx={{ display: "flex", gap: 0.5, flexDirection: "column" }}>
          <Typography variant="body2">
            {camelCaseToTitleCase(configKey)}
          </Typography>
          <HelperText text={helperText} />
        </Box>
        <IconButton
          onClick={() => append({ id: getRandomId(), value: "" })}
          size="small"
        >
          <Iconify icon="solar:add-linear" />
        </IconButton>
      </Box>
      {fields.map((field, index) => (
        <Box
          key={field.id}
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography variant="body2">variable_{index + 1}</Typography>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <FormSelectField
              control={control}
              fieldName={`${fieldName}.${index}.value`}
              sx={{ width: "200px" }}
              options={allColumns.map((col) => ({
                label: col.headerName,
                value: col.field,
              }))}
              size="small"
              MenuProps={{ sx: { maxHeight: "300px" } }}
            />
            <IconButton size="small" onClick={() => remove(index)}>
              <Iconify icon="solar:minus-linear" />
            </IconButton>
          </Box>
        </Box>
      ))}
      {(!!isError || fields.length === 0) && (
        <FormHelperText
          sx={{ paddingLeft: 1, marginTop: 0 }}
          error={!!isError || fields.length === 0}
        >
          {errorMessage}
        </FormHelperText>
      )}
    </Box>
  );
};

export const RuleStringInputVariable = ({ control, config, configKey }) => {
  const fieldName = `config.config.${configKey}`;
  const helperText =
    config?.configParamsDesc?.[configKey] ||
    config?.config_params_desc?.[configKey];

  const { fields, append, remove } = useFieldArray({
    control,
    name: fieldName,
  });

  const { errors } = useFormState({ control });

  const errorMessage =
    _.get(errors, fieldName)?.root?.message ||
    _.get(errors, fieldName)?.message ||
    "";
  const isError = !!errorMessage;

  return (
    <Box sx={{ display: "flex", gap: 1, flexDirection: "column" }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <Box sx={{ display: "flex", gap: 0.5, flexDirection: "column" }}>
          <Typography variant="body2">
            {camelCaseToTitleCase(configKey)}
          </Typography>
          <HelperText text={helperText} />
        </Box>
        <IconButton
          onClick={() => append({ id: getRandomId(), value: "" })}
          size="small"
        >
          <Iconify icon="solar:add-linear" />
        </IconButton>
      </Box>
      {fields.map((field, index) => (
        <Box
          key={field.id}
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography variant="body2">variable_{index + 1}</Typography>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <IconButton size="small" onClick={() => remove(index)}>
              <Iconify icon="solar:minus-linear" />
            </IconButton>
          </Box>
        </Box>
      ))}
      {(!!isError || fields.length === 0) && (
        <FormHelperText
          sx={{ paddingLeft: 1, marginTop: 0 }}
          error={!!isError || fields.length === 0}
        >
          {errorMessage}
        </FormHelperText>
      )}
    </Box>
  );
};

RuleStringInput.propTypes = {
  control: PropTypes.object,
  fieldConfig: PropTypes.object,
  config: PropTypes.object,
  configKey: PropTypes.string,
  allColumns: PropTypes.array,
};

RuleStringInputVariable.propTypes = {
  control: PropTypes.object,
  fieldConfig: PropTypes.object,
  config: PropTypes.object,
  configKey: PropTypes.string,
  allColumns: PropTypes.array,
};

export default RuleStringInput;
