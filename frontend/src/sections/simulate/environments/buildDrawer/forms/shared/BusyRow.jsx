import PropTypes from "prop-types";
import { CircularProgress, Stack, Typography } from "@mui/material";
import { T12 } from "../../drawerStyles";

/** A quiet "working on it" line: listing agents, reading a repository. */
export default function BusyRow({ children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      role="status"
      sx={{
        px: 1.5,
        py: 1.25,
        borderRadius: "6px",
        bgcolor: "background.neutral",
      }}
    >
      <CircularProgress
        size={13}
        thickness={5}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Typography
        component="div"
        sx={{ ...T12, color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        {children}
      </Typography>
    </Stack>
  );
}
BusyRow.propTypes = { children: PropTypes.node };
