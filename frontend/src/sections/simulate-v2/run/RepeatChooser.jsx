import PropTypes from "prop-types";
import {
  Box,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";

/* A quiet divider between kinds of repeat (25 Nov: the headings were more
   text to read; the divider is enough). */
// eslint-disable-next-line no-unused-vars
export function RepeatGroup({ children }) {
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider", my: 0.5 }} />
  );
}
RepeatGroup.propTypes = { children: PropTypes.node };

/**
 * One repeat action as one short line; what it does is on hover. Actions
 * that can't run here are left out rather than listed greyed (25 Nov: the
 * menu was too long and too wordy).
 */
export function RepeatRow({ icon, action, onClick }) {
  if (action.disabled) return null;
  return (
    <Tooltip arrow placement="left" title={action.hint || action.detail || ""}>
      <MenuItem onClick={onClick} sx={{ gap: 1, py: 0.75, minHeight: 0 }}>
        <ListItemIcon sx={{ minWidth: "0 !important" }}>
          <Iconify icon={icon} width={16} sx={{ color: "text.secondary" }} />
        </ListItemIcon>
        <ListItemText
          primary={action.label}
          primaryTypographyProps={{ typography: "s2", fontWeight: 500 }}
        />
      </MenuItem>
    </Tooltip>
  );
}
RepeatRow.propTypes = {
  icon: PropTypes.string.isRequired,
  action: PropTypes.shape({
    label: PropTypes.string,
    hint: PropTypes.string,
    detail: PropTypes.string,
    meta: PropTypes.string,
    disabled: PropTypes.string,
  }).isRequired,
  onClick: PropTypes.func,
};
