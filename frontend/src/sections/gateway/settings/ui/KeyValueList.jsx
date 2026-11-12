import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

// Label and value rows (a definition list). Two columns from sm up, stacked
// on a phone.
export default function KeyValueList({ rows }) {
  return (
    <Box
      component="dl"
      sx={{
        m: 0,
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "180px minmax(0, 1fr)" },
        columnGap: 2,
        rowGap: { xs: 0.25, sm: 1.25 },
      }}
    >
      {rows
        .filter((row) => row && !row.hidden)
        .map((row) => (
          <React.Fragment key={row.label}>
            <Typography
              component="dt"
              typography="s1"
              color="text.secondary"
              sx={{ pt: { xs: 1, sm: 0 } }}
            >
              {row.label}
            </Typography>
            <Box
              component="dd"
              sx={{
                m: 0,
                minWidth: 0,
                typography: "s1",
                color: "text.primary",
                overflowWrap: "anywhere",
              }}
            >
              {row.value}
            </Box>
          </React.Fragment>
        ))}
    </Box>
  );
}

KeyValueList.propTypes = {
  rows: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.node,
      hidden: PropTypes.bool,
    }),
  ).isRequired,
};
