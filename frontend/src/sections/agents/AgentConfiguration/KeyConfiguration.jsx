import React from "react";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import PropTypes from "prop-types";

const KeyConfiguration = ({ control, errors, required }) => {
  return (
    <>
      <FormTextFieldV2
        control={control}
        fieldName="apiKey"
        type="password"
        placeholder="Enter API key"
        label="API Key"
        fullWidth
        size="small"
        error={errors && !!errors.apiKey?.message}
        helperText={errors && errors.apiKey?.message}
        required={required}
      />
      <FormTextFieldV2
        control={control}
        fieldName="assistantId"
        placeholder="asst_xxx"
        label="Assistant ID"
        fullWidth
        size="small"
        required={required}
        error={errors && !!errors.assistantId?.message}
        helperText={errors && errors.assistantId?.message}
        sx={{
          "& .MuiInputLabel-root": {
            fontWeight: 500,
          },
        }}
      />
    </>
  );
};

KeyConfiguration.propTypes = {
  control: PropTypes.object.isRequired,
  errors: PropTypes.object.isRequired,
  required: PropTypes.bool.isRequired,
};

export default KeyConfiguration;
