import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Controller, useFormContext } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const OPERATORS = [
  { value: "eq", label: "equals (==)" },
  { value: "neq", label: "not equals (!=)" },
  { value: "gt", label: "greater than (>)" },
  { value: "gte", label: "greater than or equal (>=)" },
  { value: "lt", label: "less than (<)" },
  { value: "lte", label: "less than or equal (<=)" },
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "does not contain" },
  { value: "is_empty", label: "is empty" },
  { value: "is_not_empty", label: "is not empty" },
];

const NO_VALUE_OPERATORS = ["is_empty", "is_not_empty"];

/**
 * ConditionalNodeForm
 *
 * Lets users define a single condition:
 *   left operand  [operator]  right operand
 *
 * Both operands support {{variable}} syntax to reference upstream node outputs.
 * The "true" output handle fires when the condition passes; "false" otherwise.
 */
export default function ConditionalNodeForm({ nodeId: _nodeId }) {
  const { control, watch } = useFormContext();
  const operator = watch("operator");
  const needsRightOperand = !NO_VALUE_OPERATORS.includes(operator);

  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      <Typography variant="body2" color="text.secondary">
        The <strong>true</strong> branch runs when the condition passes; the{" "}
        <strong>false</strong> branch runs otherwise.
      </Typography>

      {/* Left operand */}
      <Box>
        <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
          Left operand
        </Typography>
        <FormTextFieldV2
          name="leftOperand"
          control={control}
          placeholder="{{variable}} or a literal value"
          size="small"
          fullWidth
          rules={{ required: "Left operand is required" }}
        />
      </Box>

      {/* Operator */}
      <Box>
        <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
          Operator
        </Typography>
        <Controller
          name="operator"
          control={control}
          defaultValue="eq"
          render={({ field }) => (
            <TextField {...field} select size="small" fullWidth>
              {OPERATORS.map((op) => (
                <MenuItem key={op.value} value={op.value}>
                  {op.label}
                </MenuItem>
              ))}
            </TextField>
          )}
        />
      </Box>

      {/* Right operand — hidden for unary operators */}
      {needsRightOperand && (
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: "block" }}>
            Right operand
          </Typography>
          <FormTextFieldV2
            name="rightOperand"
            control={control}
            placeholder="{{variable}} or a literal value"
            size="small"
            fullWidth
            rules={{ required: "Right operand is required" }}
          />
        </Box>
      )}

      <Typography variant="caption" color="text.disabled">
        Use <code>{"{{variable_name}}"}</code> to reference outputs from upstream nodes.
      </Typography>
    </Stack>
  );
}

ConditionalNodeForm.propTypes = {
  nodeId: PropTypes.string.isRequired,
};
