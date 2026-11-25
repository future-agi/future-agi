import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { camelCaseToTitleCase } from "src/utils/utils";
import HelperText from "../HelperText";
import Iconify from "src/components/iconify";
import { useFieldArray } from "react-hook-form";
import { ShowComponent } from "src/components/show";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const DictInput = ({ control, fieldConfig, config, configKey }) => {
  const label = camelCaseToTitleCase(configKey);
  const helperText =
    config?.configParamsDesc?.[configKey] ||
    config?.config_params_desc?.[configKey];

  const { append, fields, remove } = useFieldArray({
    control,
    name: `config.config.${configKey}`,
  });

  useEffect(() => {
    if (fieldConfig?.default) {
      Object.keys(fieldConfig.default).forEach((key) => {
        append({ key, value: fieldConfig.default[key] });
      });
    }
  }, [fieldConfig?.default, append]);

  return (
    <Box sx={{ display: "flex", gap: 1, flexDirection: "column" }}>
      <Box sx={{ display: "flex", gap: 0.5, flexDirection: "column" }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography variant="body2">{label}</Typography>
          <IconButton
            size="small"
            onClick={() => append({ key: "", value: "" })}
          >
            <Iconify icon="solar:add-linear" width={16} />
          </IconButton>
        </Box>
        <HelperText text={helperText} />
      </Box>
      <ShowComponent condition={Boolean(fields.length > 0)}>
        <Box sx={{ display: "flex", gap: 1.5 }}>
          <Typography sx={{ flex: 1 }} variant="body2" fontWeight={500}>
            Key
          </Typography>
          <Typography sx={{ flex: 1 }} variant="body2" fontWeight={500}>
            Value
          </Typography>
          <IconButton size="small" sx={{ visibility: "hidden" }}>
            <Iconify icon="solar:minus-linear" width={12} />
          </IconButton>
        </Box>
      </ShowComponent>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {fields.map((field, index) => (
          <Box
            key={field.id}
            sx={{ display: "flex", gap: 1.5, alignItems: "center" }}
          >
            <FormTextFieldV2
              size="small"
              control={control}
              placeholder="Enter key"
              fieldName={`config.config.${configKey}.${index}.key`}
              defaultValue={field?.key}
              fullWidth
            />
            <FormTextFieldV2
              size="small"
              control={control}
              placeholder="Enter value"
              fieldName={`config.config.${configKey}.${index}.value`}
              defaultValue={fieldConfig?.default?.[field.key]} // Access value dynamically based on the field key
              fullWidth
            />
            <IconButton size="small" onClick={() => remove(index)}>
              <Iconify icon="solar:minus-linear" width={12} />
            </IconButton>
          </Box>
        ))}
      </Box>
    </Box>
  );
};

DictInput.propTypes = {
  control: PropTypes.object,
  fieldConfig: PropTypes.object,
  config: PropTypes.object,
  configKey: PropTypes.string,
};

export default DictInput;
