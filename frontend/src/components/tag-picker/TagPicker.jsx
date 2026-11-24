import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Chip,
  Divider,
  Popover,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { getTagColor } from "./tagColors";

export function TagChip({ tag }) {
  const color = getTagColor(tag);
  return (
    <Chip
      label={tag}
      size="small"
      sx={{
        color,
        bgcolor: `${color}14`,
        border: `1px solid ${color}30`,
        pointerEvents: "none",
      }}
    />
  );
}

TagChip.propTypes = { tag: PropTypes.string.isRequired };

const TagPicker = ({
  tags,
  knownTags = [],
  onChange,
  onOpen,
  knownError = false,
  knownFetching = false,
  onRetryKnown,
  disabled = false,
  disabledReason = "",
  shown = 2,
}) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const [newTagInput, setNewTagInput] = useState("");

  const toggleTag = useCallback(
    (tag) => {
      onChange(
        tags.includes(tag) ? tags.filter((t) => t !== tag) : [...tags, tag],
      );
    },
    [tags, onChange],
  );

  const handleNewTag = useCallback(() => {
    const tag = newTagInput.trim();
    if (!tag) return;
    if (!tags.includes(tag)) onChange([...tags, tag]);
    setNewTagInput("");
  }, [newTagInput, tags, onChange]);

  const availableTags = useMemo(() => {
    const combined = new Set([...knownTags, ...tags]);
    const q = search.toLowerCase();
    return Array.from(combined)
      .filter((t) => !q || t.toLowerCase().includes(q))
      .sort();
  }, [knownTags, tags, search]);

  const open = (event) => {
    event.stopPropagation();
    if (disabled) return;
    setAnchorEl(event.currentTarget);
    onOpen?.();
  };

  return (
    <>
      <Tooltip arrow title={disabled ? disabledReason : ""}>
        <Box
          role="button"
          tabIndex={disabled ? -1 : 0}
          aria-label="Edit tags"
          aria-disabled={disabled}
          onClick={open}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") open(event);
          }}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            overflow: "hidden",
            width: "100%",
            cursor: disabled ? "default" : "pointer",
            borderRadius: "4px",
            px: 0.5,
            py: 0.25,
            "&:hover": disabled ? {} : { bgcolor: "action.hover" },
          }}
        >
          {tags.length > 0 ? (
            <>
              {tags.slice(0, shown).map((tag) => (
                <TagChip key={tag} tag={tag} />
              ))}
              {tags.length > shown && (
                <Typography sx={{ typography: "s1", color: "text.disabled" }}>
                  +{tags.length - shown}
                </Typography>
              )}
              {!disabled && (
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  width={14}
                  sx={{ color: "text.disabled", ml: "auto", flexShrink: 0 }}
                />
              )}
            </>
          ) : (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                color: "text.disabled",
              }}
            >
              <Iconify icon="solar:add-square-linear" width={20} />
            </Box>
          )}
        </Box>
      </Tooltip>

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={() => {
          setAnchorEl(null);
          setSearch("");
          setNewTagInput("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: { width: 240, borderRadius: "8px", overflow: "hidden" },
            onClick: (e) => e.stopPropagation(),
          },
        }}
      >
        <Box sx={{ p: 1.5, pb: 1 }}>
          <Typography sx={{ typography: "s1", fontWeight: 600, mb: 1 }}>
            Tags
          </Typography>
          <TextField
            size="small"
            fullWidth
            placeholder="Search tags"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
            InputProps={{
              startAdornment: (
                <Iconify
                  icon="solar:magnifier-linear"
                  width={16}
                  sx={{ color: "text.disabled", mr: 0.5 }}
                />
              ),
              sx: { typography: "s1", height: 32, borderRadius: "6px" },
            }}
          />
        </Box>

        <Box sx={{ maxHeight: 200, overflow: "auto", px: 0.5 }}>
          {availableTags.map((tag) => (
            <ButtonBase
              component="div"
              key={tag}
              onClick={() => toggleTag(tag)}
              sx={{
                justifyContent: "flex-start",
                textAlign: "left",
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                px: 1,
                py: 0.25,
                cursor: "pointer",
                borderRadius: "4px",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Checkbox
                size="small"
                checked={tags.includes(tag)}
                tabIndex={-1}
                sx={{ p: 0.25, "& .MuiSvgIcon-root": { fontSize: 16 } }}
              />
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: 500,
                  color: getTagColor(tag),
                }}
              >
                {tag}
              </Typography>
            </ButtonBase>
          ))}
          {knownError && (
            <Box
              role="alert"
              sx={{
                px: 1,
                py: 0.75,
                typography: "s1",
                color: "warning.main",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 1,
              }}
            >
              Tag suggestions unavailable.
              <Button
                size="small"
                disabled={knownFetching}
                onClick={() => onRetryKnown?.()}
              >
                Retry
              </Button>
            </Box>
          )}
          {availableTags.length === 0 && !knownError && (
            <Typography
              sx={{
                px: 1,
                py: 1,
                typography: "s1",
                color: "text.disabled",
                textAlign: "center",
              }}
            >
              No tags found
            </Typography>
          )}
        </Box>

        <Divider sx={{ mt: 0.5 }} />

        <Box sx={{ p: 1 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="Type new tag and press Enter"
            value={newTagInput}
            onChange={(e) => setNewTagInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                e.stopPropagation();
                handleNewTag();
              }
            }}
            InputProps={{
              startAdornment: (
                <Iconify
                  icon="solar:add-linear"
                  width={14}
                  sx={{ color: "primary.main", mr: 0.5 }}
                />
              ),
              sx: { typography: "s1", height: 32, borderRadius: "6px" },
            }}
          />
        </Box>
      </Popover>
    </>
  );
};

TagPicker.propTypes = {
  tags: PropTypes.arrayOf(PropTypes.string).isRequired,
  knownTags: PropTypes.arrayOf(PropTypes.string),
  onChange: PropTypes.func.isRequired,
  onOpen: PropTypes.func,
  knownError: PropTypes.bool,
  knownFetching: PropTypes.bool,
  onRetryKnown: PropTypes.func,
  disabled: PropTypes.bool,
  disabledReason: PropTypes.string,
  shown: PropTypes.number,
};

export default TagPicker;
