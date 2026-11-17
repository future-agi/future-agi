import React from "react";
import { Controller } from "react-hook-form";
import PropTypes from "prop-types";
import _ from "lodash";
import FormStaticDropdown from "./FormStaticDropdownState";
import { mergeRefs } from "src/utils/utils";

const FormStaticDropdownControl = React.forwardRef(
  /** @param {Omit<React.ComponentProps<typeof import("./index").default>, "ref">} props */ (
    { control, fieldName, label, helperText, defaultValue = "", ...rest },
    ref,
  ) => {
    return (
      <Controller
        control={control}
        name={fieldName}
        defaultValue={defaultValue}
        render={({
          field: { onChange, value, onBlur, ref: fieldRef },
          formState: { errors },
        }) => {
          const errorMessage = _.get(errors, `${fieldName}.message`);
          const isError = !!errorMessage;

          return (
            <FormStaticDropdown
              ref={mergeRefs(fieldRef, ref)}
              value={value || ""}
              onChange={(newValue) => {
                onChange(newValue);
                rest?.onChange?.(newValue);
              }}
              onBlur={onBlur}
              isError={isError}
              errorMessage={`${errorMessage}`}
              {...rest}
            />
          );
        }}
      />
    );
  },
);

FormStaticDropdownControl.displayName = "FormStaticDropdownControl";

FormStaticDropdownControl.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.oneOfType([PropTypes.string, PropTypes.number])
        .isRequired,
    }),
  ).isRequired,
  label: PropTypes.string,
  placeholder: PropTypes.string,
  helperText: PropTypes.string,
  required: PropTypes.bool,
  fullWidth: PropTypes.bool,
  disabled: PropTypes.bool,
  size: PropTypes.oneOf(["small", "medium", "large"]),
  sx: PropTypes.object,
  variant: PropTypes.oneOf(["outlined", "filled", "standard"]),
  defaultValue: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func,
};

export default FormStaticDropdownControl;
