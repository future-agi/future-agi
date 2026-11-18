import { Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback } from "react";
import { useWatch } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { ShowComponent } from "src/components/show";
import { RANGE_FILTER_OPS } from "src/api/contracts/filter-contract.generated";
import {
  AdvanceNumberFilterOperators,
  BOOLEAN_VALUE_OPTIONS,
  BooleanFilterOperators,
  FILTER_INPUT_TYPES,
  FilterDefaultOperators,
  FilterDefaultValues,
  TextFilterOperators,
} from "src/utils/constants";
import { NULL_OPERATORS } from "../../../../components/ComplexFilter/common";

const OPERATORS = {
  text: TextFilterOperators,
  boolean: BooleanFilterOperators,
  number: AdvanceNumberFilterOperators,
};

const VALUE_OPTIONS = {
  boolean: BOOLEAN_VALUE_OPTIONS,
};
const RangeOperators = new Set(RANGE_FILTER_OPS);

export default function FilterDependents({
  control,
  fieldPrefix = "",
  update,
  index,
  filter,
}) {
  const allFilters = useWatch({ control, name: "filters", defaultValue: [] });
  const currentFilter = allFilters?.[index] ?? {};
  const filter_type = currentFilter?.filterConfig?.filterType;
  const filter_op = currentFilter?.filterConfig?.filterOp;

  const handleTypeChange = useCallback(
    (e) => {
      const value = e?.target?.value;
      update({
        ...filter,
        property: "attributes",
        filterConfig: {
          filterType: value,
          filterOp: FilterDefaultOperators[value],
          filterValue: FilterDefaultValues[value],
        },
      });
    },
    [filter, update],
  );

  if (!currentFilter) return null;

  const handleUpdateOperator = (e) => {
    const newOperator = e?.target?.value;
    const filterType = filter?.filterConfig?.filterType;
    const existingValue = filter?.filterConfig?.filterValue;

    let newFilterValue;

    if (filterType === "number") {
      const isBetween = RangeOperators.has(newOperator);

      if (isBetween) {
        newFilterValue = [existingValue?.[0] ?? "", existingValue?.[1] ?? ""];
      } else {
        newFilterValue = [existingValue?.[0] ?? ""];
      }
    } else if (filterType === "text") {
      newFilterValue = existingValue ?? "";
    } else if (filterType === "boolean") {
      newFilterValue = existingValue ?? FilterDefaultValues[newOperator];
    } else if (NULL_OPERATORS.includes(newOperator)) {
      newFilterValue = "";
    } else {
      newFilterValue = FilterDefaultValues[newOperator];
    }

    update({
      ...filter,
      property: "attributes",
      filterConfig: {
        ...filter?.filterConfig,
        filterOp: newOperator,
        filterValue: newFilterValue,
      },
    });
  };

  return (
    <>
      <FormSearchSelectFieldControl
        fullWidth={false}
        fieldName={fieldPrefix ? `${fieldPrefix}.filterType` : `filterType`}
        label="Type"
        showClear={false}
        size="small"
        control={control}
        options={FILTER_INPUT_TYPES}
        onChange={handleTypeChange}
      />
      <FormSearchSelectFieldControl
        fullWidth={false}
        fieldName={fieldPrefix ? `${fieldPrefix}.filterOp` : `filterOp`}
        showClear={false}
        size="small"
        label={"Operator"}
        control={control}
        options={OPERATORS[filter_type]}
        onChange={handleUpdateOperator}
      />
      <ShowComponent condition={!NULL_OPERATORS.includes(filter_op)}>
        {filter_type === "boolean" && (
          <FormSearchSelectFieldControl
            fullWidth={false}
            fieldName={
              fieldPrefix ? `${fieldPrefix}.filterValue` : `filterValue`
            }
            showClear={false}
            size="small"
            label={"Selected value"}
            control={control}
            options={VALUE_OPTIONS[filter_type]}
          />
        )}
        {filter_type === "text" && (
          <FormTextFieldV2
            fullWidth={false}
            fieldName={
              fieldPrefix ? `${fieldPrefix}.filterValue` : `filterValue`
            }
            showClear={false}
            size="small"
            label={"Selected value"}
            control={control}
          />
        )}
        {filter_type === "number" && (
          <>
            <FormTextFieldV2
              fullWidth={false}
              fieldType="number"
              fieldName={
                fieldPrefix ? `${fieldPrefix}.filterValue[0]` : `filterValue[0]`
              }
              showClear={false}
              size="small"
              label={"Selected value"}
              control={control}
              helperText=""
              defaultValue=""
              onBlur={() => {}}
            />
            {RangeOperators.has(filter_op) && (
              <>
                <Typography
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                  variant="s1"
                >
                  and
                </Typography>
                <FormTextFieldV2
                  fullWidth={false}
                  fieldType="number"
                  fieldName={
                    fieldPrefix
                      ? `${fieldPrefix}.filterValue[1]`
                      : `filterValue[1]`
                  }
                  showClear={false}
                  size="small"
                  label={"Second value"}
                  control={control}
                  helperText=""
                  defaultValue=""
                  onBlur={() => {}}
                />
              </>
            )}
          </>
        )}
      </ShowComponent>
    </>
  );
}

FilterDependents.propTypes = {
  index: PropTypes.number,
  control: PropTypes.object,
  options: PropTypes.array,
  fieldPrefix: PropTypes.string,
  update: PropTypes.func,
  filter: PropTypes.object,
};
