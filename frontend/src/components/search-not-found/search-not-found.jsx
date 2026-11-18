import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

// ----------------------------------------------------------------------

export default function SearchNotFound({ query, sx, ...other }) {
  return query ? (
    <Box sx={{ bgcolor: "unset", textAlign: "center", ...sx }} {...other}>
      <Typography variant="h6" gutterBottom>
        Not Found
      </Typography>

      <Typography variant="body2">
        No results found for &nbsp;
        <strong>&quot;{query}&quot;</strong>.
        <br /> Try checking for typos or using complete words.
      </Typography>
    </Box>
  ) : (
    <Typography variant="body2" sx={sx}>
      Please enter keywords
    </Typography>
  );
}

SearchNotFound.propTypes = {
  query: PropTypes.string,
  sx: PropTypes.object,
};
