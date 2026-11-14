import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Iconify from "src/components/iconify";

const LABEL = "Enterprise";

/**
 * Marks an entry point to a feature this deployment's license or plan
 * doesn't include. The entry point still opens, onto the feature's upgrade
 * gate. `compact` is the lock alone (for buttons), the word in its
 * accessible name.
 */
export default function EnterpriseMark({ compact = false, sx }) {
  if (compact)
    return (
      <Box
        component="span"
        role="img"
        aria-label={`${LABEL} feature`}
        sx={{ display: "inline-flex", color: "text.disabled", ...sx }}
      >
        <Iconify icon="mdi:lock-outline" width={14} />
      </Box>
    );
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.25,
        px: 0.5,
        borderRadius: "4px",
        bgcolor: "action.hover",
        color: "text.secondary",
        fontSize: 11,
        fontWeight: 500,
        lineHeight: "18px",
        whiteSpace: "nowrap",
        ...sx,
      }}
    >
      <Iconify icon="mdi:lock-outline" width={12} />
      {LABEL}
    </Box>
  );
}

EnterpriseMark.propTypes = {
  compact: PropTypes.bool,
  sx: PropTypes.object,
};
