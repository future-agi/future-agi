import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { GATEWAY_MONO } from "../../utils/fonts";

// A titled block of label/value rows inside a gateway detail drawer: label
// s2 secondary on the left, value s1 primary on the right (stacked below sm).
// The key, provider and request (log) drawers all use these rows.
export function DetailSection({ title, action, children }) {
  return (
    <Box
      component="section"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
        }}
      >
        <Typography
          component="h3"
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          {title}
        </Typography>
        {action}
      </Box>
      {children}
    </Box>
  );
}

DetailSection.propTypes = {
  title: PropTypes.node.isRequired,
  action: PropTypes.node,
  children: PropTypes.node,
};

export function DetailRow({ label, children, mono = false }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "160px minmax(0, 1fr)" },
        columnGap: 2,
        rowGap: 0.25,
        py: 0.5,
      }}
    >
      <Typography typography="s2" color="text.secondary">
        {label}
      </Typography>
      <Typography
        component="div"
        typography={mono ? "s2_1" : "s1"}
        color="text.primary"
        sx={{
          overflowWrap: "anywhere",
          ...(mono && { fontFamily: GATEWAY_MONO }),
        }}
      >
        {children}
      </Typography>
    </Box>
  );
}

DetailRow.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
  mono: PropTypes.bool,
};

// A small figure (label over value) for the drawer's numbers row.
export function DetailFigure({ label, value }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography typography="s2" color="text.secondary" noWrap>
        {label}
      </Typography>
      <Typography typography="m3" color="text.primary" noWrap>
        {value}
      </Typography>
    </Box>
  );
}

DetailFigure.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
};
