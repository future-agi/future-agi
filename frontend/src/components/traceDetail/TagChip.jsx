import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Popover, Stack, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";
import { TAG_COLORS, tagBg } from "./tagUtils";

/* ── Color picker: row of dots ── */
const ColorDots = ({ value, onChange }) => (
  <Stack direction="row" gap="4px" sx={{ p: 0.75 }}>
    {TAG_COLORS.map((c) => (
      <Box
        key={c}
        onClick={(e) => {
          e.stopPropagation();
          onChange(c);
        }}
        sx={{
          width: 16,
          height: 16,
          borderRadius: "50%",
          bgcolor: c,
          cursor: "pointer",
          border: "2px solid",
          borderColor: c === value ? "text.primary" : "transparent",
          transition: "border-color 120ms",
        }}
      />
    ))}
  </Stack>
);

ColorDots.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

/**
 * TagChip — a single colored tag with optional edit/remove actions.
 *
 * Props:
 *   name       — tag display name
 *   color      — hex color string
 *   onRemove   — called to remove the tag
 *   onColorChange — called with new hex when color changes
 *   onRename   — called with new name when renamed
 *   size       — "small" (default, 20px) or "medium" (24px)
 *   readOnly   — hides edit/remove controls
 */
const TagChip = ({
  name,
  color,
  onRemove,
  onColorChange,
  onRename,
  size = "small",
  readOnly = false,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [colorAnchor, setColorAnchor] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(name);

  const h = size === "medium" ? 24 : 20;
  const fontSize = size === "medium" ? 12 : 11;
  const dotSize = size === "medium" ? 8 : 6;

  const handleOpenColorPicker = (e) => {
    e.stopPropagation();
    setColorAnchor(e.currentTarget);
  };

  const handleColorSelect = useCallback(
    (c) => {
      onColorChange?.(c);
      setColorAnchor(null);
    },
    [onColorChange],
  );

  const handleStartEdit = (e) => {
    if (readOnly || !onRename) return;
    e.stopPropagation();
    setEditValue(name);
    setIsEditing(true);
  };

  const handleFinishEdit = () => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== name) {
      onRename?.(trimmed);
    }
    setIsEditing(false);
  };

  return (
    <>
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: "3px",
          pl: 0.5,
          pr: readOnly ? 0.5 : 0.25,
          height: h,
          borderRadius: "4px",
          bgcolor: tagBg(color, isDark),
          fontSize,
          fontWeight: 500,
          color,
          lineHeight: `${h}px`,
          whiteSpace: "nowrap",
          "&:hover .tag-actions": { opacity: 1 },
        }}
      >
        {/* Color dot — clickable to pick color */}
        <Box
          onClick={
            !readOnly && onColorChange ? handleOpenColorPicker : undefined
          }
          sx={{
            width: dotSize,
            height: dotSize,
            borderRadius: "50%",
            bgcolor: color,
            flexShrink: 0,
            cursor: !readOnly && onColorChange ? "pointer" : "default",
          }}
        />

        {/* Name — double click to edit */}
        {isEditing ? (
          <Box
            component="input"
            autoFocus
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={handleFinishEdit}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleFinishEdit();
              }
              if (e.key === "Escape") setIsEditing(false);
            }}
            onClick={(e) => e.stopPropagation()}
            sx={{
              border: "none",
              outline: "none",
              width: Math.max(40, editValue.length * 7),
              fontSize,
              fontWeight: 500,
              color,
              bgcolor: "transparent",
              p: 0,
              minWidth: 0,
            }}
          />
        ) : (
          <Box
            component="span"
            onDoubleClick={handleStartEdit}
            sx={{
              cursor: onRename && !readOnly ? "text" : "default",
              userSelect: "none",
            }}
          >
            {name}
          </Box>
        )}

        {/* Remove button */}
        {!readOnly && onRemove && (
          <IconButton
            className="tag-actions"
            size="small"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            sx={{
              p: 0,
              opacity: 0.4,
              transition: "opacity 120ms",
              "&:hover": { bgcolor: "transparent", opacity: 1 },
            }}
          >
            <Iconify
              icon="solar:close-linear"
              width={fontSize}
              sx={{ color }}
            />
          </IconButton>
        )}
      </Box>

      {/* Color picker popover */}
      <Popover
        open={Boolean(colorAnchor)}
        anchorEl={colorAnchor}
        onClose={() => setColorAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: { borderRadius: "8px" },
          },
        }}
      >
        <ColorDots value={color} onChange={handleColorSelect} />
      </Popover>
    </>
  );
};

TagChip.propTypes = {
  name: PropTypes.string.isRequired,
  color: PropTypes.string.isRequired,
  onRemove: PropTypes.func,
  onColorChange: PropTypes.func,
  onRename: PropTypes.func,
  size: PropTypes.oneOf(["small", "medium"]),
  readOnly: PropTypes.bool,
};

export default React.memo(TagChip);
