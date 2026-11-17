import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { GATEWAY_MONO } from "../../utils/fonts";

// Label / value rows for a detail drawer: s2 secondary labels, s1 values.
// Rows whose value is null or undefined are left out.
export default function KeyValueList({ rows, labelWidth = 150 }) {
  const shown = rows.filter(
    (row) => row && row.value !== undefined && row.value !== null,
  );
  return (
    <Box
      component="dl"
      sx={{
        m: 0,
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: `${labelWidth}px 1fr` },
        columnGap: 2,
        rowGap: { xs: 0.25, sm: 1 },
      }}
    >
      {shown.map((row) => (
        <React.Fragment key={row.label}>
          <Typography
            component="dt"
            typography="s2"
            color="text.secondary"
            sx={{ pt: { sm: "2px" } }}
          >
            {row.label}
          </Typography>
          <Typography
            component="dd"
            typography="s1"
            color="text.primary"
            sx={{
              m: 0,
              mb: { xs: 1, sm: 0 },
              minWidth: 0,
              overflowWrap: "anywhere",
              fontFamily: row.mono ? GATEWAY_MONO : undefined,
              fontSize: row.mono ? 13 : undefined,
            }}
          >
            {row.value}
          </Typography>
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
      mono: PropTypes.bool,
    }),
  ).isRequired,
  labelWidth: PropTypes.number,
};
