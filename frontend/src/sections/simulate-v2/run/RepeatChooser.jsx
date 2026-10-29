import PropTypes from "prop-types";
import {
  Box,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

/* A group heading in the Repeat chooser: what the actions under it share. */
export function RepeatGroup({ children }) {
  return (
    <Typography
      sx={{
        px: 2,
        pt: 1.25,
        pb: 0.25,
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        letterSpacing: 0.4,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      {children}
    </Typography>
  );
}
RepeatGroup.propTypes = { children: PropTypes.node };

/**
 * One repeat action: what it does, then whether it makes new agent calls,
 * what happens to state and what it costs. Unsupported — still listed,
 * disabled, the reason on hover and under it.
 */
export function RepeatRow({ icon, action, onClick }) {
  const row = (
    <MenuItem
      onClick={onClick}
      disabled={!!action.disabled}
      sx={{
        alignItems: "flex-start",
        gap: 0.75,
        py: 1,
        whiteSpace: "normal",
        "&.Mui-disabled": { opacity: 1 },
      }}
    >
      <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
        <Iconify
          icon={icon}
          width={16}
          sx={{ color: action.disabled ? "text.disabled" : "text.primary" }}
        />
      </ListItemIcon>
      <ListItemText
        primary={action.label}
        secondary={
          <>
            {action.detail}
            {(action.disabled || action.meta) && (
              <Box
                component="span"
                sx={{
                  display: "block",
                  mt: 0.25,
                  color: action.disabled ? "text.disabled" : "text.secondary",
                }}
              >
                {action.disabled
                  ? `Unavailable — ${action.disabled}`
                  : action.meta}
              </Box>
            )}
          </>
        }
        primaryTypographyProps={{
          typography: "s2",
          fontWeight: 600,
          color: action.disabled ? "text.disabled" : "text.primary",
        }}
        secondaryTypographyProps={{
          typography: "s3",
          color: action.disabled ? "text.disabled" : "text.tertiary",
          sx: { whiteSpace: "normal" },
        }}
      />
    </MenuItem>
  );
  return action.disabled ? (
    <Tooltip arrow placement="left" title={action.disabled}>
      <Box>{row}</Box>
    </Tooltip>
  ) : (
    row
  );
}
RepeatRow.propTypes = {
  icon: PropTypes.string.isRequired,
  action: PropTypes.shape({
    label: PropTypes.string,
    detail: PropTypes.string,
    meta: PropTypes.string,
    disabled: PropTypes.string,
  }).isRequired,
  onClick: PropTypes.func,
};
