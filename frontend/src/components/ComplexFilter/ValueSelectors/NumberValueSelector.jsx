import { TextField, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { AdvanceNumberFilterOperators } from "src/utils/constants";
import { handleNumericInput } from "../common";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { RANGE_FILTER_OPS } from "src/api/contracts/filter-contract.generated";

const RangeOperators = new Set(RANGE_FILTER_OPS);

const NumberValueSelector = ({ definition, filter, updateFilter }) => {
  const values = filter.filter_config;

  const operators =
    definition?.overrideOperators || AdvanceNumberFilterOperators;

  return (
    <>
      <FormSearchSelectFieldState
        onChange={(e) => {
          updateFilter(filter.id, (existingFilter) => ({
            ...existingFilter,
            filter_config: {
              ...existingFilter.filter_config,
              filter_op: e.target.value,
            },
          }));
        }}
        label=""
        value={values?.filter_op || ""}
        size="small"
        options={operators.map(({ label, value }) => ({
          label,
          value,
        }))}
      />
      <TextField
        sx={{
          width: "80px",
        }}
        type="text"
        label="Value"
        placeholder="Value"
        size="small"
        value={values?.filter_value?.[0] || ""}
        onChange={(e) => {
          const value = handleNumericInput(e.target.value);
          updateFilter(filter.id, (existingFilter) => ({
            ...existingFilter,
            filter_config: {
              ...existingFilter.filter_config,
              filter_value: [
                value,
                existingFilter?.filter_config?.filter_value?.[1] || "",
              ],
            },
          }));
        }}
      />
      {RangeOperators.has(values?.filter_op) ? (
        <>
          <Typography
            variant="s2"
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            and
          </Typography>
          <TextField
            sx={{ width: "80px" }}
            type="text"
            placeholder="Value"
            size="small"
            value={values?.filter_value?.[1] || ""}
            onChange={(e) => {
              const value = handleNumericInput(e.target.value);
              updateFilter(filter.id, (existingFilter) => ({
                ...existingFilter,
                filter_config: {
                  ...existingFilter.filter_config,
                  filter_value: [
                    existingFilter?.filter_config?.filter_value?.[0] || "",
                    value,
                  ],
                },
              }));
            }}
          />
        </>
      ) : (
        <></>
      )}
    </>
  );
};

NumberValueSelector.propTypes = {
  definition: PropTypes.object,
  filter: PropTypes.object,
  updateFilter: PropTypes.func,
};

export default NumberValueSelector;
