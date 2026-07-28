import { FormControl, TextField, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
// 'greater_than'
// 'less_than'
// 'equals'
// 'not_equals'
// 'greater_than_or_equal'
// 'less_than_or_equal'
// 'between'
// 'not_between'
const NumberFilterOperators = [
  { label: "Greater Than", value: "greater_than" },
  { label: "Less Than", value: "less_than" },
  { label: "Equals", value: "equals" },
  { label: "Not Equals", value: "not_equals" },
  { label: "Greater Than Or Equal", value: "greater_than_or_equal" },
  { label: "Less Than Or Equal", value: "less_than_or_equal" },
  { label: "Between", value: "between" },
  { label: "Not Between", value: "not_between" },
];

const DevelopNumberFilter = ({ filter, updateFilter }) => {
  const multipleInput = ["between", "not_between"].includes(
    filter.filterConfig.filterOp,
  );

  const getValue = (indetity) => {
    if (multipleInput) {
      return filter.filterConfig.filterValue[indetity];
    } else {
      return filter.filterConfig.filterValue;
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
      if (multipleInput) newValue[0] = parseFloat(value);
      else newValue = parseFloat(value);
    } else {
      newValue[1] = parseFloat(value);
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
        <TextField
          size="small"
          label="Value"
          type="number"
          value={getValue(1)}
          onChange={(e) => updater(1, e.target.value)}
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
          options={NumberFilterOperators}
          // MenuProps={{ sx: { maxHeight: "300px" } }}
          onChange={(e) => {
            updateFilter(filter.id, (internalFilter) => ({
              ...internalFilter,
              filterConfig: {
                ...internalFilter.filterConfig,
                filterOp: e.target.value,
              },
            }));
          }}
        />
      </FormControl>
      <TextField
        size="small"
        label="Value"
        type="number"
        value={getValue(0)}
        onChange={(e) => updater(0, e.target.value)}
      />
      {["between", "not_between"].includes(filter.filterConfig.filterOp) &&
        renderExtraInput()}
    </>
  );
};

DevelopNumberFilter.propTypes = {
  filter: PropTypes.object,
  updateFilter: PropTypes.func,
};

export default DevelopNumberFilter;
