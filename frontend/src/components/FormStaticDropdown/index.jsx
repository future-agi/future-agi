import React from "react";
import PropTypes from "prop-types";
import FormStaticDropdownControl from "./FormStaticDropdownControl";
import FormStaticDropdownState from "./FormStaticDropdownState";

const dropdownPropTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string,
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
  errorMessage: PropTypes.string,
  isError: PropTypes.bool,
  required: PropTypes.bool,
  fullWidth: PropTypes.bool,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  defaultValue: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func,
  disabled: PropTypes.bool,
  size: PropTypes.oneOf(["small", "medium", "large"]),
  sx: PropTypes.object,
  variant: PropTypes.oneOf(["outlined", "filled", "standard"]),
};

const FormStaticDropdown = React.forwardRef(
  /** @param {PropTypes.InferProps<typeof dropdownPropTypes>} props */
  ({ control, ...rest }, ref) => {
    if (control) {
      return (
        <FormStaticDropdownControl
          ref={ref}
          fieldName={rest?.fieldName}
          label={rest?.label}
          helperText={rest?.helperText}
          control={control}
          {...rest}
        />
      );
    }
    return <FormStaticDropdownState ref={ref} {...rest} />;
  },
);

FormStaticDropdown.displayName = "FormStaticDropdown";
FormStaticDropdown.propTypes = dropdownPropTypes;
export default FormStaticDropdown;
