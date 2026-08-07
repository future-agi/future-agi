import React, { useState, useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import { TAG_COLORS, hashColor } from "./tagUtils";

/**
 * TagInput — input field for creating a new tag with color selection.
 *
 * Props:
 *   onAdd          — called with { name, color } when user presses Enter
 *   onCancel       — called when user presses Escape or input blurs empty
 *   existingNames  — array of names to prevent duplicates
 *   suggestions    — existing project tags to suggest while typing
 *   disabled       — disables input
 *   placeholder    — placeholder text
 *   autoFocus      — auto-focus on mount
 *   compact        — smaller variant for inline use
 */
const TagInput = ({
  onAdd,
  onCancel,
  existingNames = [],
  suggestions = [],
  disabled = false,
  placeholder = "Add tag...",
  autoFocus = true,
  compact = false,
}) => {
  const [value, setValue] = useState("");
  const [selectedColor, setSelectedColor] = useState(null);

  const normalizedExistingNames = useMemo(
    () => new Set(existingNames.map((name) => name.trim().toLowerCase())),
    [existingNames],
  );

  const matchingSuggestions = useMemo(() => {
    const query = value.trim().toLowerCase();
    if (!query) return [];

    return suggestions
      .map((suggestion) =>
        typeof suggestion === "string"
          ? { name: suggestion, color: hashColor(suggestion) }
          : suggestion,
      )
      .filter((suggestion) => {
        const name = suggestion?.name?.trim();
        return (
          name &&
          name.toLowerCase().includes(query) &&
          !normalizedExistingNames.has(name.toLowerCase())
        );
      })
      .slice(0, 8);
  }, [normalizedExistingNames, suggestions, value]);

  const previewColor =
    selectedColor || (value.trim() ? hashColor(value.trim()) : TAG_COLORS[0]);

  const handleSubmit = useCallback(() => {
    const name = value.trim();
    if (!name) return;
    if (
      existingNames.some(
        (existingName) =>
          existingName.trim().toLowerCase() === name.toLowerCase(),
      )
    ) {
      setValue("");
      return;
    }
    onAdd({ name, color: selectedColor || hashColor(name) });
    setValue("");
    setSelectedColor(null);
  }, [value, selectedColor, existingNames, onAdd]);

  const handleSelectSuggestion = useCallback(
    (suggestion) => {
      const name = suggestion?.name?.trim();
      if (!name) return;
      onAdd({ name, color: suggestion.color || hashColor(name) });
      setValue("");
      setSelectedColor(null);
    },
    [onAdd],
  );

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (matchingSuggestions.length > 0) {
          handleSelectSuggestion(matchingSuggestions[0]);
        } else {
          handleSubmit();
        }
      }
      if (e.key === "Escape") {
        onCancel?.();
      }
    },
    [handleSelectSuggestion, handleSubmit, matchingSuggestions, onCancel],
  );

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
      <Box
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
            transition: "transform 100ms, background-color 150ms",
            "&:hover": { transform: "scale(1.3)" },
          }}
        />
        <Box
          component="input"
          autoFocus={autoFocus}
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => {
            if (!value.trim()) onCancel?.();
          }}
          disabled={disabled}
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
            <Iconify icon="mdi:keyboard-return" width={compact ? 12 : 14} />
          </Box>
        )}
      </Box>

      {matchingSuggestions.length > 0 && (
        <Box
          role="listbox"
          aria-label="Existing project tags"
          sx={{
            maxHeight: 160,
            overflowY: "auto",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            py: 0.25,
          }}
        >
          {matchingSuggestions.map((suggestion) => (
            <Box
              key={suggestion.name}
              role="option"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => handleSelectSuggestion(suggestion)}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.75,
                px: 1,
                py: 0.5,
                cursor: "pointer",
                fontSize,
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Box
                sx={{
                  width: dotSize,
                  height: dotSize,
                  borderRadius: "50%",
                  bgcolor: suggestion.color || hashColor(suggestion.name),
                  flexShrink: 0,
                }}
              />
              <Box component="span">{suggestion.name}</Box>
            </Box>
          ))}
        </Box>
      )}

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
                transition: "transform 100ms, border-color 100ms",
                "&:hover": { transform: "scale(1.2)" },
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
  suggestions: PropTypes.arrayOf(
    PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.shape({
        name: PropTypes.string.isRequired,
        color: PropTypes.string,
      }),
    ]),
  ),
  disabled: PropTypes.bool,
  placeholder: PropTypes.string,
  autoFocus: PropTypes.bool,
  compact: PropTypes.bool,
};

export default React.memo(TagInput);
