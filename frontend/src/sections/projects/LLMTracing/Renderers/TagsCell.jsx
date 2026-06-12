import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Chip } from "@mui/material";
import { normalizeTag } from "src/components/traceDetail/tagUtils";
import TagChip from "src/components/traceDetail/TagChip";
import AddTagsPopover from "src/components/traceDetail/AddTagsPopover";

const MAX_VISIBLE = 2;

const TagsCell = ({ value, traceId, spanId }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const tags = Array.isArray(value) ? value : [];
  // Only rows that carry a trace/span id can mutate tags. Without one there is
  // nothing to PATCH, so the cell stays a passive (non-clickable) display.
  const editable = Boolean(traceId || spanId);

  if (tags.length === 0 && !editable) return null;

  const visible = tags.slice(0, MAX_VISIBLE);
  const overflowCount = tags.length - MAX_VISIBLE;

  const handleOpen = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  return (
    <>
      <Box
        onClick={editable ? handleOpen : undefined}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 1.5,
          overflow: "hidden",
          height: "100%",
          cursor: editable ? "pointer" : "default",
        }}
      >
        {visible.map((rawTag, idx) => {
          const tag = normalizeTag(rawTag);
          return (
            <TagChip
              key={`${tag.name}-${idx}`}
              name={tag.name}
              color={tag.color}
              size="small"
              readOnly
            />
          );
        })}
        {overflowCount > 0 && (
          <Chip
            label={`+${overflowCount}`}
            size="small"
            sx={{
              height: 20,
              fontSize: 11,
              "& .MuiChip-label": { px: 0.75 },
              bgcolor: "action.hover",
            }}
          />
        )}
        {tags.length === 0 && editable && (
          <Chip
            label="+ Tag"
            size="small"
            variant="outlined"
            sx={{
              height: 20,
              fontSize: 11,
              color: "text.secondary",
              borderStyle: "dashed",
              "& .MuiChip-label": { px: 0.75 },
            }}
          />
        )}
      </Box>
      {editable && (
        <AddTagsPopover
          open={Boolean(anchorEl)}
          anchorEl={anchorEl}
          onClose={() => setAnchorEl(null)}
          traceId={traceId}
          spanId={spanId}
          currentTags={value}
        />
      )}
    </>
  );
};

TagsCell.propTypes = {
  value: PropTypes.array,
  traceId: PropTypes.string,
  spanId: PropTypes.string,
};

export default React.memo(TagsCell);
