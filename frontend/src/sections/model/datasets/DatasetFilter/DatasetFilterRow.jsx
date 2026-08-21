import {
  Box,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

import StringConditionalFilter from "./StringConditionalFilter";
import NumberConditionalFilter from "./NumberConditionalFilter";
import DateConditionalFilter from "./DateConditionalFilter";

const DatasetFilterRow = ({
  properties,
  setValuesForIndex,
  filter,
  idx,
  options,
  removeFilter,
}) => {
  const dataType = filter.dataType;

  const renderConditionalFilter = () => {
    if (dataType === "string") {
      return (
        <StringConditionalFilter
          options={options}
          filter={filter}
          setValue={(v) => setValuesForIndex(idx, v)}
        />
      );
    } else if (dataType === "number") {
      return (
        <NumberConditionalFilter
          filter={filter}
          setValue={(v) => setValuesForIndex(idx, v)}
        />
      );
    } else if (dataType === "date") {
      return (
        <DateConditionalFilter
          filter={filter}
          setValue={(v) => setValuesForIndex(idx, v)}
        />
      );
    }
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
      <FormControl sx={{ width: "264px" }} size="small">
        <InputLabel>Property</InputLabel>
        <Select
          value={filter.key}
          onChange={(e) => {
            const newValue = e.target.value;
            const property = properties.find((p) => p.value === newValue);
            if (!property) return;
            setValuesForIndex(idx, {
              key: newValue,
              dataType: property.dataType,
              value: [],
              operator: "equal",
            });
          }}
          label="Property"
        >
          {properties.map(({ label, value }) => (
            <MenuItem value={value} key={value}>
              {label}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      {renderConditionalFilter()}
      {
        <IconButton onClick={() => removeFilter(idx)}>
          <Iconify
            icon="solar:trash-bin-trash-bold"
            sx={{ color: "primary.light" }}
          />
        </IconButton>
      }
    </Box>
  );
};

DatasetFilterRow.propTypes = {
  properties: PropTypes.array,
  setValuesForIndex: PropTypes.func,
  idx: PropTypes.number,
  filter: PropTypes.object,
  options: PropTypes.array,
  removeFilter: PropTypes.func,
  totalFilters: PropTypes.number,
};

export default DatasetFilterRow;
