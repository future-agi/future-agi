// @ts-nocheck
import { Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";

const OptionValueSelector = ({ definition, filter, updateFilter }) => {
  const values = filter.filter_config;

  // Initialize selected values
  const selectedValues = Array.isArray(values.filter_value)
    ? values.filter_value.filter(Boolean)
    : typeof values.filter_value === "string"
      ? values.filter_value.split(",").filter(Boolean)
      : [];

  // Set up React Hook Form
  const { control, watch, setValue } = useForm({
    defaultValues: {
      selectedOptions: definition.multiSelect
        ? selectedValues
        : selectedValues[0] || "",
    },
  });

  // Watch for changes in the form
  const watchedValue = watch("selectedOptions");

  // Update filter when form value changes
  useEffect(() => {
    if (watchedValue !== undefined) {
      const updatedValues = definition.multiSelect
        ? Array.isArray(watchedValue)
          ? watchedValue
          : []
        : watchedValue;

      updateFilter(filter.id, (existingFilter) => ({
        ...existingFilter,
        filter_config: {
          ...existingFilter.filter_config,
          filter_value: updatedValues,
          filter_op: definition.multiSelect ? "contains" : "equals",
        },
      }));
    }
  }, [watchedValue, definition.multiSelect, filter.id, updateFilter]);

  // Prepare options for the FormSearchSelectFieldControl
  const options =
    definition.filterType?.options.map(({ label, value }) => ({
      label: label,
      value: value,
    })) || [];

  // Update form value when filter changes externally
  useEffect(() => {
    const currentSelectedValues = Array.isArray(values.filter_value)
      ? values.filter_value.filter(Boolean)
      : typeof values.filter_value === "string"
        ? values.filter_value.split(",").filter(Boolean)
        : [];

    const newValue = definition.multiSelect
      ? currentSelectedValues
      : currentSelectedValues[0] || "";

    setValue("selectedOptions", newValue);
  }, [values.filter_value, definition.multiSelect, setValue]);

  return (
    <>
      <Typography
        variant="s1"
        fontWeight={"fontWeightRegular"}
        color="text.primary"
      >
        is
      </Typography>
      <FormSearchSelectFieldControl
        label={definition?.propertyName}
        size="small"
        control={control}
        sx={{ maxWidth: "200px", width: "100%" }}
        fieldName="selectedOptions"
        options={options}
        multiple={definition.multiSelect}
        checkbox={definition.multiSelect}
      />
    </>
  );
};

OptionValueSelector.propTypes = {
  definition: PropTypes.object,
  filter: PropTypes.object,
  updateFilter: PropTypes.func,
};

export default OptionValueSelector;
