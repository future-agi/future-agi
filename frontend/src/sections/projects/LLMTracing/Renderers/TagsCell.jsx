import React, { useCallback, useState } from "react";
import PropTypes from "prop-types";
import { Box, Chip } from "@mui/material";
import AddTagsPopover from "src/components/traceDetail/AddTagsPopover";
import { normalizeTag } from "src/components/traceDetail/tagUtils";
import TagChip from "src/components/traceDetail/TagChip";

const MAX_VISIBLE = 2;

const TagsCell = ({ value, traceId, spanId }) => {
  const tags = Array.isArray(value) ? value : [];
  const canEdit = Boolean(traceId || spanId);
  const [anchorEl, setAnchorEl] = useState(null);

  const handleOpen = useCallback(
    (event) => {
      if (!canEdit) return;
      event.stopPropagation();
      setAnchorEl(event.currentTarget);
    },
    [canEdit],
  );

  const handleKeyDown = useCallback(
    (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      handleOpen(event);
    },
    [handleOpen],
  );

  const handleClose = useCallback(() => {
    setAnchorEl(null);
  }, []);

  if (tags.length === 0) {
    if (!canEdit) return null;

    return (
      <>
        <Box
          role="button"
          tabIndex={0}
          onClick={handleOpen}
          onKeyDown={handleKeyDown}
          sx={{
            display: "flex",
            alignItems: "center",
            px: 1.5,
            height: "100%",
            cursor: "pointer",
          }}
        >
          <Chip
            label="+ Tag"
            size="small"
            variant="outlined"
            sx={{ height: 20, fontSize: 11, "& .MuiChip-label": { px: 0.75 } }}
          />
        </Box>
        <AddTagsPopover
          open={Boolean(anchorEl)}
          anchorEl={anchorEl}
          onClose={handleClose}
          traceId={traceId}
          spanId={spanId}
          currentTags={tags}
        />
      </>
    );
  }

  const visible = tags.slice(0, MAX_VISIBLE);
  const overflowCount = tags.length - MAX_VISIBLE;

  return (
    <>
      <Box
        role={canEdit ? "button" : undefined}
        tabIndex={canEdit ? 0 : undefined}
        onClick={handleOpen}
        onKeyDown={canEdit ? handleKeyDown : undefined}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 1.5,
          overflow: "hidden",
          height: "100%",
          cursor: canEdit ? "pointer" : "default",
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
            onClick={handleOpen}
            sx={{
              height: 20,
              fontSize: 11,
              "& .MuiChip-label": { px: 0.75 },
              bgcolor: "action.hover",
              cursor: canEdit ? "pointer" : "default",
            }}
          />
        )}
      </Box>
      {canEdit && (
        <AddTagsPopover
          open={Boolean(anchorEl)}
          anchorEl={anchorEl}
          onClose={handleClose}
          traceId={traceId}
          spanId={spanId}
          currentTags={tags}
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
