import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

// A heading inside a tab or drawer: s1 medium, an optional one-line note and
// right-hand actions.
export default function SectionTitle({
  title,
  note,
  actions,
  component = "h2",
}) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: { xs: "flex-start", sm: "center" },
        justifyContent: "space-between",
        flexDirection: { xs: "column", sm: "row" },
        gap: 1,
      }}
    >
      <Box sx={{ minWidth: 0 }}>
        <Typography
          component={component}
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          {title}
        </Typography>
        {note && (
          <Typography typography="s2" color="text.secondary">
            {note}
          </Typography>
        )}
      </Box>
      {actions && (
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>{actions}</Box>
      )}
    </Box>
  );
}

SectionTitle.propTypes = {
  title: PropTypes.node.isRequired,
  note: PropTypes.node,
  actions: PropTypes.node,
  component: PropTypes.string,
};
