import PropTypes from "prop-types";
import { Field } from "../formBits";

const SECRET_HELPER =
  "Stored encrypted and used only by Future AGI. Never shown again, never sent to the improver.";

/**
 * A credential field in a setup form. Keys are pasted directly, stored
 * encrypted, and never shown again.
 */
export default function SecretField({
  label,
  required,
  value,
  onChange,
  placeholder = "Paste the key",
  helper = SECRET_HELPER,
  sx,
}) {
  return (
    <Field
      label={label}
      required={required}
      mono
      type="password"
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      helper={helper}
      sx={sx}
    />
  );
}
SecretField.propTypes = {
  label: PropTypes.node,
  required: PropTypes.bool,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  helper: PropTypes.node,
  sx: PropTypes.object,
};
