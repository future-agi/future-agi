import React from "react";
import { Box, TextField } from "@mui/material";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";

const InputPromptBoxCopy = ({
  placeholder,
  minRows,
  maxRows,
  onChange,
  value,
}) => {
  const theme = useTheme();

  return (
    <Box
      sx={{
        paddingTop: 0.5,
        width: "100%",
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: "8px",
        position: "relative",
        backgroundColor: "action.hover",
      }}
    >
      <TextField
        value={value}
        onChange={(e) => onChange(e.target.value)}
        multiline
        minRows={minRows}
        maxRows={maxRows}
        fullWidth
        placeholder={placeholder}
        InputProps={{
          disableUnderline: true,
        }}
        sx={{
          "&.MuiInputBase-input": {
            padding: 0,
            border: "none",
            boxShadow: "none",
          },
          "& .MuiInputBase-input::placeholder": {
            fontSize: "13px",
          },
          border: "none",
          boxShadow: "none",
          padding: "5px",
          paddingLeft: "12px",
        }}
      />
    </Box>
  );
};

InputPromptBoxCopy.propTypes = {
  placeholder: PropTypes.string.isRequired,
  minRows: PropTypes.number,
  maxRows: PropTypes.number,
  onChange: PropTypes.func.isRequired,
  value: PropTypes.string,
};

export default InputPromptBoxCopy;
