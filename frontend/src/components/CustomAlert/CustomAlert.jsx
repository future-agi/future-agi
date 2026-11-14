import { Alert, AlertTitle } from "@mui/material";
import PropTypes from "prop-types";

// Banner styling comes from the theme's MuiAlert override (status tint, border
// and icon colour). "ghost" is the same alert without the surface, for use
// inside a section that already has one.
const GHOST_SX = {
  backgroundColor: "transparent",
  borderColor: "transparent",
  padding: 0,
};

/**
 * @typedef {'info' | 'success' | 'warning' | 'error'} AlertVariant
 * @typedef {'standard' | 'ghost'} AlertType
 */

/**
 * @param {Object} props
 * @param {AlertVariant} [props.variant='info'] - The alert variant (info, success, warning, error)
 * @param {AlertType} [props.type='standard'] - The alert type (standard with background or ghost without background)
 * @param {string} [props.title] - The alert title
 * @param {string} props.message - The alert message
 * @param {Function} [props.onClose] - Callback fired when the close button is clicked
 * @param {Object} [props.sx] - Additional MUI sx styling
 */
export const CustomAlert = ({
  variant = "info",
  type = "standard",
  title,
  message,
  onClose,
  sx,
}) => {
  return (
    <Alert
      severity={variant}
      onClose={onClose}
      sx={[type === "ghost" && GHOST_SX, ...(Array.isArray(sx) ? sx : [sx])]}
    >
      {title && <AlertTitle>{title}</AlertTitle>}
      {message}
    </Alert>
  );
};

CustomAlert.propTypes = {
  variant: PropTypes.oneOf(["info", "success", "warning", "error"]),
  type: PropTypes.oneOf(["standard", "ghost"]),
  title: PropTypes.string,
  message: PropTypes.string.isRequired,
  onClose: PropTypes.func,
  sx: PropTypes.object,
};
