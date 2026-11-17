import React from "react";
import PropTypes from "prop-types";
import { Box, TextField, Typography } from "@mui/material";
import { Controller, useWatch } from "react-hook-form";

export { webhookPayload } from "./task_webhook";

const TaskWebhookFields = ({ control, disabled = false }) => {
  const url = useWatch({ control, name: "webhook_url" });
  const operator = useWatch({ control, name: "webhook_condition.operator" });
  const evals = useWatch({ control, name: "evalsDetails" }) || [];
  const runType = useWatch({ control, name: "runType" });

  const field = (name, label, options = {}) => (
    <Controller
      name={name}
      control={control}
      defaultValue=""
      render={({ field: input, fieldState: { error } }) => (
        <TextField
          {...input}
          value={input.value ?? ""}
          label={label}
          fullWidth
          size="small"
          disabled={disabled}
          {...options}
          error={!!error}
          helperText={error?.message || options.helperText}
        />
      )}
    />
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, py: 2 }}>
      <Typography variant="subtitle2">Result webhook (optional)</Typography>
      <Typography variant="body2" color="text.secondary">
        Send matching span or trace results to a public HTTP(S) endpoint.
        {runType !== "continuous" && " Delivery runs only for continuous tasks."}
      </Typography>
      {field("webhook_url", "Webhook URL", { type: "url" })}
      {url && <>
        {field("webhook_condition.eval_config_id", "Webhook evaluation", {
          select: true, SelectProps: { native: true }, InputLabelProps: { shrink: true },
          children: <>
            <option value="">Select evaluation</option>
            {evals.map((evaluation) => <option key={evaluation.id} value={evaluation.id}>
              {evaluation.name || evaluation.eval_template_name || "Evaluation"}
            </option>)}
          </>,
        })}
        {field("webhook_condition.operator", "Webhook operator", {
          select: true, SelectProps: { native: true }, InputLabelProps: { shrink: true },
          children: <>
            <option value="">Select operator</option>
            <option value="gt">Greater than</option>
            <option value="lt">Less than</option>
            <option value="eq">Equals</option>
            <option value="fail">Fail (boolean false)</option>
          </>,
        })}
        {operator !== "fail" && field("webhook_condition.value", "Webhook value", {
          helperText: operator === "eq" ? "Text, JSON number or boolean; quote numeric strings." : "Numeric threshold",
        })}
        <Typography variant="caption" color="text.secondary">
          Results include evaluation values and explanations. Delivery retries once;
          the receiving endpoint should tolerate duplicates. Clear the URL to disable.
        </Typography>
      </>}
    </Box>
  );
};

TaskWebhookFields.propTypes = {
  control: PropTypes.object.isRequired,
  disabled: PropTypes.bool,
};

export default TaskWebhookFields;
