import React from "react";
import PropTypes from "prop-types";
import { TextField } from "@mui/material";

const SearchBar = ({ value, onChange }) => (
  <TextField
    size="small"
    fullWidth
    value={value}
    onChange={(event) => onChange(event.target.value)}
    placeholder="Search evaluations or spans"
    inputProps={{ "aria-label": "Search evaluations or spans" }}
  />
);

SearchBar.propTypes = { value: PropTypes.string.isRequired, onChange: PropTypes.func.isRequired };

export default SearchBar;
