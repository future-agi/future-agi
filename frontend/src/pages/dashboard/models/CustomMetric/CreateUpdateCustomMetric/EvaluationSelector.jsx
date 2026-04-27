import {
  Box,
  FormControlLabel,
  FormGroup,
  FormHelperText,
  Radio,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { useController } from "react-hook-form";

const EvaluationSelector = ({ control }) => {
  const { field, fieldState } = useController({
    control: control,
    name: "evaluationType",
  });

  const isError = !!fieldState.error?.message;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", paddingX: 1 }}>
      <FormGroup row>
        <FormControlLabel
          control={<Radio defaultChecked />}
          label="Evaluate Output"
          componentsProps={{ typography: { color: "text.disabled" } }}
          checked={field.value === "EVALUATE_CHAT"}
          onChange={(_, c) => field.onChange(c ? "EVALUATE_CHAT" : null)}
        />
        <FormControlLabel
          control={<Radio />}
          label="Evaluate Rag Context"
          color="text.disabled"
          componentsProps={{ typography: { color: "text.disabled" } }}
          checked={field.value === "EVALUATE_CONTEXT"}
          onChange={(_, c) => field.onChange(c ? "EVALUATE_CONTEXT" : null)}
        />
        {/* <FormControlLabel
          control={<Radio />}
          label="Evaluate Prompt Template"
          color="text.disabled"
          componentsProps={{ typography: { color: "text.disabled" } }}
          checked={field.value === "EVALUATE_PROMPT_TEMPLATE"}
          onChange={(_, c) =>
            field.onChange(c ? "EVALUATE_PROMPT_TEMPLATE" : null)
          }
        /> */}
        {/* <FormControlLabel  control={<Checkbox />} label="Evaluate context ranking" /> */}
      </FormGroup>
      {isError && (
        <FormHelperText sx={{ marginTop: 0 }} error>
          {fieldState.error?.message}
        </FormHelperText>
      )}
    </Box>
  );
};

EvaluationSelector.propTypes = {
  control: PropTypes.object,
};

export default EvaluationSelector;
