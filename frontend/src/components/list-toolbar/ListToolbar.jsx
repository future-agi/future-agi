import PropTypes from "prop-types";
import { Stack } from "@mui/material";

// ----------------------------------------------------------------------
// Future AGI Design System · List toolbar. The one wrapper for the row above
// a table. Children go in this order: search, segmented toggle, filter
// selects, <Box sx={{ flex: 1 }} /> spacer, actions. Rhythm: tabs → toolbar
// 12px, toolbar → table 8px. Sibling tabs render the same wrapper so
// switching tabs moves nothing.
// ----------------------------------------------------------------------

export default function ListToolbar({ children, sx, ...other }) {
  return (
    <Stack
      direction="row"
      spacing={1}
      alignItems="center"
      flexShrink={0}
      sx={{ pt: 1.5, pb: 1, ...sx }}
      {...other}
    >
      {children}
    </Stack>
  );
}

ListToolbar.propTypes = {
  children: PropTypes.node,
  sx: PropTypes.object,
};
