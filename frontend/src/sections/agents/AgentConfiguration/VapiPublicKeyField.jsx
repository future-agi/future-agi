import React from "react";
import PropTypes from "prop-types";
import { Button, Stack } from "@mui/material";
import { useWatch } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

// The saved key is never sent to the browser. The field is write-only: blank
// keeps the saved key, a typed value replaces it, and "Remove saved key" asks
// the server to delete it.
const VapiPublicKeyField = ({ control, setValue }) => {
  const hasSavedKey = useWatch({ control, name: "hasVapiPublicKey" });
  const removeSavedKey = useWatch({ control, name: "removeVapiPublicKey" });
  const saved = hasSavedKey === true && removeSavedKey !== true;

  let helperText =
    "Needed only when the assistant has authentication on in Vapi.";
  if (saved) helperText = "Saved (leave blank to keep it)";
  if (removeSavedKey === true) helperText = "The saved key is removed on save.";

  return (
    <Stack spacing={1} alignItems="flex-start" sx={{ width: "100%" }}>
      <FormTextFieldV2
        control={control}
        fieldName="vapiPublicKey"
        fieldType="password"
        placeholder={saved ? "Saved" : "Enter Vapi public key"}
        label="Vapi public key"
        helperText={helperText}
        autoComplete="new-password"
        fullWidth
        size="small"
        onChange={() => setValue("removeVapiPublicKey", false)}
      />
      {saved ? (
        <Button
          size="small"
          color="error"
          onClick={() => {
            setValue("vapiPublicKey", "");
            setValue("removeVapiPublicKey", true, { shouldDirty: true });
          }}
        >
          Remove saved key
        </Button>
      ) : null}
    </Stack>
  );
};

VapiPublicKeyField.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
};

export default VapiPublicKeyField;
