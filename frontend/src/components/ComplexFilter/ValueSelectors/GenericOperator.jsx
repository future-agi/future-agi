import PropTypes from "prop-types";
import React from "react";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import {
  BooleanFilterOperators,
  TextFilterOperators,
} from "src/utils/constants";
import { NULL_OPERATORS } from "../common";

const OPERATORS = {
  text: TextFilterOperators,
  boolean: BooleanFilterOperators,
};

export default function GenericOperator({ definition, filter, updateFilter }) {
  const values = filter?.filter_config;

  const operators =
    definition?.overrideOperators || OPERATORS[values?.filter_type] || [];

  return (
    <FormSearchSelectFieldState
      size="small"
      showClear={false}
      label={"Operator"}
      options={operators}
      value={values?.filter_op || ""}
      onChange={(e) => {
        updateFilter(filter.id, (existingFilter) => ({
          ...existingFilter,
          filter_config: {
            ...existingFilter.filter_config,
            filter_op: e.target.value,
            ...(NULL_OPERATORS.includes(e.target.value) && {
              filter_value: "",
            }),
          },
        }));
      }}
    />
  );
}

GenericOperator.propTypes = {
  definition: PropTypes.object,
  filter: PropTypes.object,
  updateFilter: PropTypes.func,
};
