import { useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, TextField } from "@mui/material";

import Iconify from "src/components/iconify";

import { canonicalizeTag } from "../constant";

/**
 * Free-text input for adding a custom tag to an eval.
 *
 * On commit it resolves the typed text against the predefined tags first
 * (so "image" selects the existing "Image" chip instead of creating a
 * near-duplicate), otherwise adds a canonical UPPERCASE_UNDERSCORE value.
 * Duplicates (case/spacing-insensitive) are ignored.
 */
export default function CustomTagInput({
  predefinedTags = [],
  selected = [],
  onAdd,
  disabled = false,
}) {
  const [text, setText] = useState("");

  const commit = () => {
    const canon = canonicalizeTag(text);
    if (!canon) return;

    const match = predefinedTags.find(
      (t) =>
        canonicalizeTag(t.value) === canon ||
        canonicalizeTag(t.label) === canon,
    );
    const value = match ? match.value : canon;

    const already = selected.some(
      (t) => canonicalizeTag(t) === canonicalizeTag(value),
    );
    if (!already) onAdd(value);
    setText("");
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 0.75 }}>
      <TextField
        size="small"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
        }}
        placeholder="Add custom tag"
        disabled={disabled}
        sx={{
          width: 180,
          "& .MuiInputBase-input": { fontSize: 12, py: 0.5 },
        }}
      />
      <IconButton
        size="small"
        onClick={commit}
        disabled={disabled || !text.trim()}
        aria-label="Add custom tag"
      >
        <Iconify icon="mdi:plus" width={16} />
      </IconButton>
    </Box>
  );
}

CustomTagInput.propTypes = {
  predefinedTags: PropTypes.array,
  selected: PropTypes.array,
  onAdd: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
