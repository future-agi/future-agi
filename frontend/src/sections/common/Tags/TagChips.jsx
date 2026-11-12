// W5-3: coloured tag chips (registry colours; a rename or recolour follows
// because chips read the registry by id).
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tooltip } from "@mui/material";
import { tagColor } from "./tagModel";

export function TagChip({ tag, size = "small", onClick }) {
  const c = tagColor(tag.color);
  return (
    <Tooltip
      title={tag.description || ""}
      disableHoverListener={!tag.description}
    >
      <Box
        component="span"
        data-testid="tag-chip"
        data-tag-id={tag.id}
        onClick={onClick}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          px: 0.75,
          height: size === "small" ? 18 : 22,
          borderRadius: 9,
          fontSize: size === "small" ? 11 : 12,
          lineHeight: 1,
          whiteSpace: "nowrap",
          color: c,
          border: `1px solid ${c}55`,
          bgcolor: `${c}14`,
          cursor: onClick ? "pointer" : "default",
        }}
      >
        <Box
          component="span"
          sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: c }}
        />
        {tag.name}
      </Box>
    </Tooltip>
  );
}

TagChip.propTypes = {
  tag: PropTypes.object.isRequired,
  size: PropTypes.string,
  onClick: PropTypes.func,
};

export default function TagChips({ tags, max, size }) {
  const list = tags ?? [];
  if (!list.length) return null;
  const shown = max ? list.slice(0, max) : list;
  const more = list.length - shown.length;
  return (
    <Stack
      direction="row"
      gap={0.5}
      flexWrap="wrap"
      alignItems="center"
      data-testid="tag-chips"
    >
      {shown.map((t) => (
        <TagChip key={t.id} tag={t} size={size} />
      ))}
      {more > 0 ? (
        <Tooltip
          title={list
            .slice(shown.length)
            .map((t) => t.name)
            .join(", ")}
        >
          <Box component="span" sx={{ fontSize: 11, color: "text.secondary" }}>
            +{more}
          </Box>
        </Tooltip>
      ) : null}
    </Stack>
  );
}

TagChips.propTypes = {
  tags: PropTypes.array,
  max: PropTypes.number,
  size: PropTypes.string,
};
