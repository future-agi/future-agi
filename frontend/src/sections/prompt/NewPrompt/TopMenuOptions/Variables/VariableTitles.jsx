import TextField from "@mui/material/TextField/TextField";
import PropTypes from "prop-types";
import React, { useState } from "react";

const VariableTitles = ({ item }) => {
  const [title, setTitle] = useState(item || "CUSTOMER_EMAIL");
  const [editing, setEditing] = useState(false);
  const handleChange = (e) => {
    setTitle(e.target.value);
  };
  const handleBlur = () => {
    setEditing(false);
  };
  return (
    <TextField
      value={title}
      onChange={handleChange}
      onBlur={handleBlur}
      autoFocus={editing}
      inputProps={{
        style: {
          textTransform: "none",
        },
      }}
      sx={{
        fontSize: "16px",
        "& .MuiInputBase-input": {
          padding: 0,
        },
      }}
      InputProps={{ disableUnderline: true }}
    />
  );
};

export default VariableTitles;

VariableTitles.propTypes = {
  item: PropTypes.string.isRequired,
};
