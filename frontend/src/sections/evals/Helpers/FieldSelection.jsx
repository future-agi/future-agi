import { Box, TextField } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const FieldSelection = ({ field }) => {
  return (
    <Box sx={{ display: "flex", gap: 3.5, alignItems: "center" }}>
      <TextField
        value={field}
        InputProps={{
          readOnly: true,
          endAdornment: (
            <Iconify icon="solar:lock-linear" color="text.disabled" />
          ),
        }}
        sx={{
          width: "150px",
        }}
        size="small"
      />
    </Box>
  );
};

FieldSelection.propTypes = {
  field: PropTypes.string,
  allColumns: PropTypes.array,
  control: PropTypes.object,
  isMultipleColumn: PropTypes.bool,
};

export default FieldSelection;
