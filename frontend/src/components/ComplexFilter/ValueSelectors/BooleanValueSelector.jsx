import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";

const BooleanValueSelector = ({ definition, filter, updateFilter }) => {
  const values = filter.filter_config;

  const options = [
    {
      label: definition.filterType.truthLabel,
      value: true,
    },
    {
      label: definition.filterType.falseLabel,
      value: false,
    },
  ];

  return (
    <>
      <Typography variant="s1">is</Typography>
      <FormSearchSelectFieldState
        label={definition?.propertyName}
        value={values?.filter_value}
        size="small"
        onChange={(e) => {
          updateFilter(filter.id, (existingFilter) => ({
            ...existingFilter,
            filter_config: {
              ...existingFilter.filter_config,
              filter_value: e.target.value,
            },
          }));
        }}
        options={options.map(({ label, value }) => ({
          label,
          value,
        }))}
        sx={{ minWidth: "130px" }}
      />
    </>
  );
};

BooleanValueSelector.propTypes = {
  definition: PropTypes.object,
  filter: PropTypes.object,
  updateFilter: PropTypes.func,
};

export default BooleanValueSelector;
