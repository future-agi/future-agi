import { FormControl, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";

const DateTimeFilterOperators = [
  { label: "Greater Than", value: "greater_than" },
  { label: "Less Than", value: "less_than" },
  { label: "Equals", value: "equals" },
  { label: "Not Equals", value: "not_equals" },
  { label: "Greater Than Or Equal", value: "greater_than_or_equal" },
  { label: "Less Than Or Equal", value: "less_than_or_equal" },
  { label: "Between", value: "between" },
  { label: "Not Between", value: "not_between" },
];

const DevelopDateTimeFilter = ({ filter, updateFilter }) => {
  const multipleInput = ["between", "not_between"].includes(
    filter.filterConfig.filterOp,
  );

  const getValue = (indetity) => {
    if (multipleInput) {
      return filter.filterConfig?.filterValue?.[indetity];
    } else {
      return filter.filterConfig?.filterValue;
    }
  };

  const updater = (indetity, value) => {
    let newValue;
    if (multipleInput) {
      newValue = [getValue(0), getValue(1)];
    } else {
      newValue = "";
    }

    if (indetity === 0) {
      if (multipleInput) newValue[0] = value;
      else newValue = value;
    } else {
      newValue[1] = value;
    }

    updateFilter(filter.id, (internalFilter) => ({
      ...internalFilter,
      filterConfig: {
        ...internalFilter.filterConfig,
        filterValue: newValue,
      },
    }));
  };

  const renderExtraInput = () => {
    return (
      <>
        <Typography variant="body2">and</Typography>
        <DatePicker
          slotProps={{
            textField: { size: "small" },
          }}
          sx={{ width: "160px" }}
          inputProps={{
            readOnly: true,
          }}
          value={getValue(1) || null}
          onChange={(v) => updater(1, v)}
        />
      </>
    );
  };

  return (
    <>
      <FormControl size="small">
        <FormSearchSelectFieldState
          value={filter.filterConfig.filterOp}
          label="Operator"
          showClear={false}
          size="small"
          onChange={(e) => {
            updateFilter(filter.id, (internalFilter) => ({
              ...internalFilter,
              filterConfig: {
                ...internalFilter.filterConfig,
                filterOp: e.target.value,
              },
            }));
          }}
          options={DateTimeFilterOperators}
        />
      </FormControl>
      <DatePicker
        slotProps={{
          textField: { size: "small" },
        }}
        sx={{ width: "160px" }}
        inputProps={{
          readOnly: true,
        }}
        value={getValue(0) || null}
        onChange={(v) => updater(0, v)}
      />
      {["between", "not_between"].includes(filter.filterConfig.filterOp) &&
        renderExtraInput()}
    </>
  );
};

DevelopDateTimeFilter.propTypes = {
  filter: PropTypes.object,
  updateFilter: PropTypes.func,
};

export default DevelopDateTimeFilter;
