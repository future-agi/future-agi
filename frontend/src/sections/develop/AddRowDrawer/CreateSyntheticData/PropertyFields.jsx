import { Box, Typography } from "@mui/material";
import React, { useEffect, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import CreateNewProperties from "./CreateNewProperties";
import PropTypes from "prop-types";
import CategoricalFields from "./CategoricalFields";
import { useFieldArray, useFormContext } from "react-hook-form";
import { LoadingButton } from "@mui/lab";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const numberFields = {
  min_length: "number",
  max_length: "number",
};

const menus = [
  { label: "Min Length", value: "min_length" },
  { label: "Max Length", value: "max_length" },
  { label: "Value", value: "value" },
];

const PropertyFields = ({ control, nestIndex, newProps = [], addNProps }) => {
  const [options, setOptions] = useState([...menus]);

  useEffect(() => {
    if (newProps) {
      setOptions([...menus, ...newProps]);
    }
  }, [newProps]);

  const [isDistributionUniform, setIsDistributionUniform] = useState(false);

  const [createNew, setCreateNew] = useState(undefined);
  const { fields, append, remove, update, move } = useFieldArray({
    name: `columns.${nestIndex}.property`,
    control,
  });

  const { watch, setError, clearErrors } = useFormContext();

  const columnValue = watch(`columns.${nestIndex}`);

  const handleAddProperty = (newItem, focusOption) => {
    append(newItem || { type: "", value: "", category: "" }, focusOption);
  };

  const updatePropertyValues = () => {
    const selectedFields = {};
    columnValue?.property?.forEach((field, i) => {
      if (
        field.category &&
        field.category !== "dynamic" &&
        field.category !== "categorical"
      ) {
        selectedFields[i] = field;
      }
    });
    const updatingValue = Object.entries(selectedFields);
    const length = updatingValue.length || 1;

    const baseValue = Math.floor(100 / length);
    let remainder = 100 - baseValue * length;

    updatingValue.forEach(([index, field]) => {
      const value = baseValue + (remainder > 0 ? 1 : 0);
      remainder--;
      update(Number(index), { ...field, value: value.toString() });
    });
  };

  const hideMinMax = {
    boolean: true,
    datetime: true,
  };

  const fieldOption = useMemo(() => {
    let newOptions = [...options];
    if (hideMinMax[columnValue.data_type]) {
      newOptions = options.filter(
        (item) => !(item.value === "min_length" || item.value === "max_length"),
      );
    }
    return newOptions;
  }, [columnValue?.data_type, options]);

  const handleMove = () => {
    let hasCategory = null;
    columnValue?.property?.forEach((item, index) => {
      if (
        item.category &&
        item.category !== "categorical" &&
        item.category !== "dynamic"
      ) {
        if (!hasCategory) {
          hasCategory = true;
        }
      }
      if (hasCategory) {
        if (!item.category) {
          if (item.type || item.value) {
            move(index, index + 1);
          } else {
            remove(index);
          }
        }
      }
    });
  };

  const validateNumber = (e, item, index) => {
    const value = e.target.value;
    if (item.type === "min_length") {
      if (value == 0) {
        setError(`columns.${nestIndex}.property.${index}.value`, {
          message: "Min length should be atleast 1",
        });
      } else {
        clearErrors(`columns.${nestIndex}.property.${index}.value`);
      }
    }
  };

  useEffect(() => {
    updatePropertyValues();
  }, [fields.length]);

  return (
    <React.Fragment>
      {fields.map((item, index) => {
        const typeValue = watch(`columns.${nestIndex}.property.${index}.type`);
        const enteredValue = watch(
          `columns.${nestIndex}.property.${index}.value`,
        );
        const categoryType = watch(
          `columns.${nestIndex}.property.${index}.category`,
        );
        const selectedOption = fieldOption.filter((item) => {
          const matched = columnValue.property.some(
            (temp) => temp?.type === item?.value && !typeValue,
          );
          return !matched;
        });
        return (
          <React.Fragment key={item.id}>
            <Box
              sx={{
                width: "100%",
                display: "flex",
                gap: "12px",
                alignItems: "flex-start",
              }}
            >
              {categoryType ? (
                <FormTextFieldV2
                  placeholder="Distribution"
                  label="Distribution"
                  size="small"
                  control={control}
                  fieldName={`columns.${nestIndex}.property.${index}.type`}
                  fullWidth={categoryType}
                />
              ) : (
                <FormSearchSelectFieldControl
                  fullWidth={typeValue}
                  control={control}
                  fieldName={`columns.${nestIndex}.property.${index}.type`}
                  size="small"
                  label="Property"
                  options={selectedOption}
                  createLabel="Add New Property"
                  handleCreateLabel={() => setCreateNew(index)}
                  sx={typeValue ? {} : { width: "200px" }}
                />
              )}
              {categoryType && (
                <Typography
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                  variant="s1"
                >
                  is
                </Typography>
              )}
              {(typeValue || categoryType) && (
                <React.Fragment>
                  {typeValue === "value" ? (
                    <FormSearchSelectFieldControl
                      fullWidth
                      control={control}
                      fieldName={`columns.${nestIndex}.property.${index}.value`}
                      size="small"
                      label="Value"
                      options={[
                        { label: "Dynamic", value: "dynamic" },
                        { label: "Categorical", value: "categorical" },
                      ]}
                    />
                  ) : (
                    <FormTextFieldV2
                      placeholder="Enter value"
                      fieldType={
                        categoryType
                          ? "number"
                          : numberFields[typeValue] || "text"
                      }
                      label="Value"
                      size="small"
                      control={control}
                      onChange={(e) => validateNumber(e, item, index)}
                      fieldName={`columns.${nestIndex}.property.${index}.value`}
                      fullWidth
                    />
                  )}
                </React.Fragment>
              )}
              <Iconify
                icon="solar:minus-linear"
                sx={{
                  width: "40px",
                  color: "text.primary",
                  cursor: "pointer",
                  marginTop: (theme) => theme.spacing(1.25),
                }}
                onClick={() => {
                  remove(index);
                  if (isDistributionUniform) {
                    updatePropertyValues();
                  }
                }}
              />
              {categoryType && <Box sx={{ width: "40px" }} />}
            </Box>
            {enteredValue === "categorical" && (
              <CategoricalFields
                remove={remove}
                handleAddProperty={handleAddProperty}
                fields={control}
                fieldName={`columns.${nestIndex}.property`}
                handleMove={handleMove}
                update={updatePropertyValues}
                isDistributionUniform={isDistributionUniform}
                setIsDistributionUniform={setIsDistributionUniform}
              />
            )}
          </React.Fragment>
        );
      })}
      <LoadingButton
        size="small"
        variant="outlined"
        type="button"
        sx={{
          width: "209px",
          borderColor: "divider",
          color: "text.secondary",
          "&:hover": {
            borderColor: "divider",
            backgroundColor: "action.hover",
          },
        }}
        startIcon={<Iconify icon="solar:add-linear" color="text.primary" />}
        onClick={() => handleAddProperty(null)}
      >
        Add more properties
      </LoadingButton>
      <CreateNewProperties
        open={Boolean(createNew === 0 ? true : createNew)}
        onClose={() => setCreateNew(undefined)}
        data={createNew}
        setOptions={addNProps}
        update={update}
      />
    </React.Fragment>
  );
};

export default PropertyFields;

PropertyFields.propTypes = {
  control: PropTypes.any,
  nestIndex: PropTypes.number,
  newProps: PropTypes.array,
  addNProps: PropTypes.func,
};
