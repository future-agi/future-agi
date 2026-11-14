import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Autocomplete, Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { TAG_COLORS, hashColor } from "./tagUtils";
import { useProjectTagNames } from "./useProjectTagNames";

/**
 * TagInput — input field for creating a new tag with color selection.
 *
 * Props:
 *   onAdd          — called with { name, color } when user presses Enter or
 *                    picks a suggested tag
 *   onCancel       — called when user presses Escape or input blurs empty
 *   existingNames  — array of names to prevent duplicates; never suggested
 *   projectId      — project whose existing tags are suggested while typing;
 *                    without it the input works with no suggestions
 *   disabled       — disables input
 *   placeholder    — placeholder text
 *   autoFocus      — auto-focus on mount
 *   compact        — smaller variant for inline use
 */
const TagInput = ({
  onAdd,
  onCancel,
  existingNames = [],
  projectId,
  disabled = false,
  placeholder = "Add tag...",
  autoFocus = true,
  compact = false,
}) => {
  const [value, setValue] = useState("");
  const [selectedColor, setSelectedColor] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const { names: projectTagNames, isLoading: suggestionsLoading } =
    useProjectTagNames({ projectId, search: value });
  const query = value.trim().toLowerCase();
  const suggestions = query
    ? projectTagNames.filter(
        (name) =>
          !existingNames.includes(name) && name.toLowerCase().includes(query),
      )
    : [];
  // Open only when there is something to show, so Escape and blur behave as
  // they always have while a new tag name matches nothing.
  const suggestionsOpen =
    menuOpen && (suggestions.length > 0 || suggestionsLoading);

  const previewColor =
    selectedColor || (value.trim() ? hashColor(value.trim()) : TAG_COLORS[0]);

  const addTag = useCallback(
    (name, color) => {
      if (!name) return;
      if (existingNames.includes(name)) {
        setValue("");
        return;
      }
      onAdd({ name, color: color || hashColor(name) });
      setValue("");
      setSelectedColor(null);
    },
    [existingNames, onAdd],
  );

  const handleSubmit = () => addTag(value.trim(), selectedColor);

  const handleChange = (_event, picked, reason) => {
    if (typeof picked !== "string") return;
    if (reason === "selectOption") {
      // A suggestion is an existing tag: keep its exact name and the colour
      // a plain string tag is shown with.
      addTag(picked, hashColor(picked));
    } else if (reason === "createOption") {
      addTag(picked.trim(), selectedColor);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter") e.preventDefault();
    if (e.key === "Escape" && !suggestionsOpen) onCancel?.();
  };

  const handleCycleColor = () => {
    const current =
      selectedColor || (value.trim() ? hashColor(value.trim()) : TAG_COLORS[0]);
    const idx = TAG_COLORS.indexOf(current);
    setSelectedColor(TAG_COLORS[(idx + 1) % TAG_COLORS.length]);
  };

  const fontSize = compact ? 11 : 12;
  const dotSize = compact ? 10 : 12;
  const paletteSize = compact ? 12 : 14;

  return (
    <Stack gap={0.5}>
      <Autocomplete
        freeSolo
        disableClearable
        disabled={disabled}
        options={suggestions}
        filterOptions={(options) => options}
        value={null}
        inputValue={value}
        onInputChange={(_event, next, reason) => {
          if (reason !== "reset") setValue(next);
        }}
        onChange={handleChange}
        open={suggestionsOpen}
        onOpen={() => setMenuOpen(true)}
        onClose={() => setMenuOpen(false)}
        loading={suggestionsLoading}
        loadingText="Loading tags…"
        onKeyDown={handleKeyDown}
        ListboxProps={{ "aria-label": "Existing tags" }}
        renderOption={(optionProps, name) => {
          const { key, ...liProps } = optionProps;
          return (
            <Box component="li" key={key} {...liProps} sx={{ gap: 1 }}>
              <Box
                sx={{
                  width: dotSize,
                  height: dotSize,
                  borderRadius: "50%",
                  bgcolor: hashColor(name),
                  flexShrink: 0,
                }}
              />
              <Typography variant="s1" noWrap>
                {name}
              </Typography>
            </Box>
          );
        }}
        renderInput={(params) => (
          <Box
            ref={params.InputProps.ref}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              border: "1px solid",
              borderColor: value.trim() ? previewColor : "divider",
              borderRadius: "4px",
              px: compact ? 0.5 : 1,
              py: compact ? 0.25 : 0.5,
              transition: "border-color 150ms",
            }}
          >
            {/* Color dot — click to cycle */}
            <Box
              onClick={handleCycleColor}
              sx={{
                width: dotSize,
                height: dotSize,
                borderRadius: "50%",
                bgcolor: previewColor,
                cursor: "pointer",
                flexShrink: 0,
                transition: "background-color 150ms",
              }}
            />
            <Box
              component="input"
              {...params.inputProps}
              aria-label="Tag name"
              autoFocus={autoFocus}
              placeholder={placeholder}
              onBlur={(e) => {
                params.inputProps.onBlur?.(e);
                if (!value.trim()) onCancel?.();
              }}
              sx={{
                border: "none",
                outline: "none",
                flex: 1,
                fontSize,
                color: "text.primary",
                bgcolor: "transparent",
                minWidth: compact ? 60 : 80,
                "&::placeholder": { color: "text.disabled" },
              }}
            />
            {value.trim() && (
              <Box
                onMouseDown={(e) => e.preventDefault()}
                onClick={handleSubmit}
                sx={{
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  color: previewColor,
                  "&:hover": { opacity: 0.7 },
                }}
              >
                <Iconify
                  icon="solar:arrow-left-down-linear"
                  width={compact ? 12 : 14}
                />
              </Box>
            )}
          </Box>
        )}
      />

      {/* Color palette — shown when user is typing */}
      {value.trim() && (
        <Stack direction="row" gap="3px" sx={{ pl: 0.25 }}>
          {TAG_COLORS.map((c) => (
            <Box
              key={c}
              onMouseDown={(e) => e.preventDefault()} // prevent blur
              onClick={() => setSelectedColor(c)}
              sx={{
                width: paletteSize,
                height: paletteSize,
                borderRadius: "50%",
                bgcolor: c,
                cursor: "pointer",
                border: "2px solid",
                borderColor:
                  c === previewColor ? "text.primary" : "transparent",
                transition: "border-color 120ms",
              }}
            />
          ))}
        </Stack>
      )}
    </Stack>
  );
};

TagInput.propTypes = {
  onAdd: PropTypes.func.isRequired,
  onCancel: PropTypes.func,
  existingNames: PropTypes.arrayOf(PropTypes.string),
  projectId: PropTypes.string,
  disabled: PropTypes.bool,
  placeholder: PropTypes.string,
  autoFocus: PropTypes.bool,
  compact: PropTypes.bool,
};

export default React.memo(TagInput);
