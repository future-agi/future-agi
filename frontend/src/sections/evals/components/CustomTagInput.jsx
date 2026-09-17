import PropTypes from "prop-types";
import TextField from "@mui/material/TextField";

// Free-text tag entry, shared by the create page and the detail page so the two
// cannot drift. Enter commits the trimmed value; an Enter that only confirms an
// IME composition is ignored.
export default function CustomTagInput({ value, onChange, onAdd, sx }) {
  return (
    <TextField
      size="small"
      placeholder="Add custom tag..."
      helperText="Press Enter to add"
      inputProps={{ "aria-label": "Add custom tag" }}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
        event.preventDefault();
        const newTag = value.trim();
        if (!newTag) return;
        onAdd(newTag);
        onChange("");
      }}
      sx={{ mt: 1.5, minWidth: 200, ...sx }}
    />
  );
}

CustomTagInput.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  onAdd: PropTypes.func.isRequired,
  sx: PropTypes.object,
};
