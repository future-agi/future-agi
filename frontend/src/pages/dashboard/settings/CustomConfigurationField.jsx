import { LoadingButton } from "@mui/lab";
import { Box, Divider, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useFieldArray } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";

const CustomConfigurationField = ({
  control,
  fieldName,
  comonAttribute,
  onFocusInput,
}) => {
  const { fields, append, remove } = useFieldArray({
    control,
    name: fieldName,
  });
  return (
    <React.Fragment>
      <Box>
        <Divider sx={{ marginBottom: "10px" }} />
        <Typography
          sx={{
            fontWeight: 600,
            fontSize: "13px",
            lineHeight: "18px",
          }}
        >
          Add Custom Configuration
        </Typography>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "end",
          }}
        >
          <HelperText text="Create your custom configuration for new model" />
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            loading={false}
            onClick={() => append({ key: "", value: "" })}
          >
            Add
          </LoadingButton>
        </Box>
      </Box>
      {fields?.map((item, index) => {
        return (
          <Box key={item.id} sx={{ display: "flex", gap: "10px" }}>
            <FormTextFieldV2
              {...comonAttribute}
              control={control}
              fieldName={`${fieldName}.${index}.key`}
              label="Custom Key"
              placeholder="Enter custom key"
              disabled={!!item?.disabled}
            />
            <FormTextFieldV2
              {...comonAttribute}
              control={control}
              fieldName={`${fieldName}.${index}.value`}
              label="Custom Value"
              placeholder="Enter custom value"
              onFocus={onFocusInput(`${fieldName}.${index}.value`)}
            />
            {item?.key !== "api_base" ? (
              <IconButton
                onClick={() => remove(index)}
                aria-label={`Remove config ${index + 1}`}
              >
                <Iconify icon="solar:trash-bin-trash-linear" />
              </IconButton>
            ) : (
              fields.length > 1 && <Box sx={{ width: "85px" }} />
            )}
          </Box>
        );
      })}
    </React.Fragment>
  );
};

export default CustomConfigurationField;

CustomConfigurationField.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string,
  comonAttribute: PropTypes.object,
  onFocusInput: PropTypes.func,
};
