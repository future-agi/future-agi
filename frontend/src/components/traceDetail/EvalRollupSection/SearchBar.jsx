import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

const SearchBar = ({ value, onChange }) => (
  <Box
    sx={{
      px: 1.5,
      py: 0.75,
      borderBottom: "1px solid",
      borderColor: "divider",
      flexShrink: 0,
      display: "flex",
      gap: 0.75,
      alignItems: "center",
    }}
  >
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        flex: 1,
        px: 0.75,
        py: 0.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
      }}
    >
      <Iconify icon="mdi:magnify" width={12} color="text.disabled" />
      <Box
        component="input"
        placeholder="Search evals..."
        value={value}
        onChange={(e) => onChange(e.target.value)}
        sx={{
          border: "none",
          outline: "none",
          flex: 1,
          fontSize: 11,
          color: "text.primary",
          bgcolor: "transparent",
          py: 0.15,
          "&::placeholder": { color: "text.disabled" },
        }}
      />
    </Box>
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        px: 1,
        py: 0.35,
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: "4px",
        bgcolor: "background.paper",
        flexShrink: 0,
        opacity: 0.7,
      }}
    >
      <Iconify icon="mdi:plus-circle-outline" width={13} color="text.disabled" />
      <Typography sx={{ fontSize: 11, fontWeight: 500, color: "text.disabled" }}>
        Add Evals
      </Typography>
      <Box
        sx={{
          px: 0.6,
          py: 0.1,
          borderRadius: "3px",
          bgcolor: (t) => alpha(t.palette.success.main, 0.16),
          color: "success.dark",
          fontSize: 9,
          fontWeight: 700,
          letterSpacing: 0.2,
          lineHeight: 1.5,
          whiteSpace: "nowrap",
        }}
      >
        Coming soon
      </Box>
    </Box>
  </Box>
);

SearchBar.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
};

export default SearchBar;
