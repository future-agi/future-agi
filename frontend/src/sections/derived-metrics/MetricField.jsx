import { Children } from "react";
import PropTypes from "prop-types";
import { TextField } from "@mui/material";

/** Readable, focusable definition values without an editable select affordance. */
export default function MetricField({
  readOnly,
  select,
  children,
  value,
  onChange,
  InputProps,
  ...props
}) {
  const selected =
    select && readOnly
      ? Children.toArray(children).find((child) => child.props?.value === value)
      : null;
  return (
    <TextField
      size="small"
      {...props}
      select={select && !readOnly}
      value={select && readOnly ? selected?.props.children ?? value : value}
      InputProps={{ ...InputProps, readOnly: readOnly || InputProps?.readOnly }}
      onChange={readOnly ? undefined : onChange}
    >
      {select && !readOnly ? children : undefined}
    </TextField>
  );
}
MetricField.propTypes = {
  readOnly: PropTypes.bool,
  select: PropTypes.bool,
  children: PropTypes.node,
  value: PropTypes.any,
  onChange: PropTypes.func,
  InputProps: PropTypes.object,
};
