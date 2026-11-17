import React from "react";
import PropTypes from "prop-types";
import { useWatch } from "react-hook-form";
import { Box, Stack } from "@mui/material";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { RANGE_FILTER_OPS } from "src/api/contracts/filter-contract.generated";

const RangeOperators = new Set(RANGE_FILTER_OPS);

// Each field keeps a typed minus, so the form's own rule can say why a
// negative is refused instead of quietly turning -5 into 5.
const NumberQuickFilterValue = ({ control }) => {
  const operator = useWatch({ control, name: "operator" });

  if (RangeOperators.has(operator)) {
    return (
      <Stack direction="row" gap={1} alignItems="flex-start">
        <FormTextFieldV2
          control={control}
          placeholder="Enter min value"
          fieldName="value1"
          label="Min"
          size="small"
          fullWidth
          sx={{ width: "100px" }}
          fieldType="number"
          allowNegative
        />
        <Box sx={{ pt: 1 }}>-</Box>
        <FormTextFieldV2
          control={control}
          placeholder="Enter max value"
          fieldName="value2"
          label="Max"
          size="small"
          fullWidth
          sx={{ maxWidth: "120px" }}
          fieldType="number"
          allowNegative
        />
      </Stack>
    );
  }

  return (
    <FormTextFieldV2
      control={control}
      placeholder="Enter value"
      fieldName="value1"
      label="Selected Value"
      size="small"
      fullWidth
      fieldType="number"
      allowNegative
    />
  );
};

NumberQuickFilterValue.propTypes = {
  control: PropTypes.object,
};

export default NumberQuickFilterValue;
