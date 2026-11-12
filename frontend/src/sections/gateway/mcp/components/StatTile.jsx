import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

// One figure on an overview: s2 label, m2 value, an optional s2 note.
export default function StatTile({ label, value, note, loading = false }) {
  return (
    <Box
      role="group"
      aria-label={label}
      sx={{
        flex: "1 1 180px",
        minWidth: 0,
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        display: "flex",
        flexDirection: "column",
        gap: 0.25,
      }}
    >
      <Typography typography="s2" color="text.secondary">
        {label}
      </Typography>
      {loading ? (
        <Skeleton variant="text" width={64} sx={{ typography: "m2" }} />
      ) : (
        <Typography
          typography="m2"
          color="text.primary"
          fontWeight="fontWeightMedium"
        >
          {value}
        </Typography>
      )}
      {note && !loading && (
        <Typography typography="s2" color="text.secondary">
          {note}
        </Typography>
      )}
    </Box>
  );
}

StatTile.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
  note: PropTypes.node,
  loading: PropTypes.bool,
};
