import { Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { AdvanceNumberFilterOperators } from "src/utils/constants";
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { RANGE_FILTER_OPS } from "src/api/contracts/filter-contract.generated";

const RangeOperators = new Set(RANGE_FILTER_OPS);

const DateValueSelector = ({ definition, filter, updateFilter }) => {
  const values = filter.filter_config;

  const operators =
    definition?.overrideOperators || AdvanceNumberFilterOperators;

  // Helper function to parse date values
  const parseDate = (dateValue) => {
    if (!dateValue || dateValue === "") return null;
    return new Date(dateValue);
  };

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
      <DatePicker
        slotProps={{
          textField: {
            size: "small",
          },
        }}
        sx={{ width: "160px" }}
        value={parseDate(values?.filter_value?.[0])}
        onChange={(v) => {
          updateFilter(filter.id, (existingFilter) => ({
            ...existingFilter,
            filter_config: {
              ...existingFilter.filter_config,
              filter_value: [
                v ?? "",
                existingFilter?.filter_config?.filter_value?.[1] || "",
              ],
            },
          }));
        }}
      />

      {RangeOperators.has(values?.filter_op) ? (
        <>
          <Typography variant="body2" color="text.disabled">
            and
          </Typography>
          <DatePicker
            slotProps={{
              textField: { size: "small" },
            }}
            sx={{ width: "160px" }}
            value={parseDate(values?.filter_value?.[1])}
            onChange={(v) => {
              updateFilter(filter.id, (existingFilter) => ({
                ...existingFilter,
                filter_config: {
                  ...existingFilter.filter_config,
                  filter_value: [
                    existingFilter?.filter_config?.filter_value?.[0] || "",
                    v ?? "",
                  ],
                },
              }));
            }}
          />
        </>
      ) : null}
    </>
  );
};

DateValueSelector.propTypes = {
  definition: PropTypes.object,
  filter: PropTypes.object,
  updateFilter: PropTypes.func,
};

export default DateValueSelector;
