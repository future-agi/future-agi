// HO D4: the optional Row info cells (Comments, Assigned to, Tags) and
// their column definitions. One read per dataset, shared by every cell
// (react-query caches it under the W4 / W5 keys).
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useAssignments, useDatasetComments } from "src/api/develop/row-collab";
import TagChips from "src/sections/common/Tags/TagChips";
import { useTags } from "src/sections/common/Tags/useTags";
import {
  assigneesByRow,
  commentsByRow,
  commentsCellText,
  rowTagNames,
  tagRecordsFor,
} from "./collabColumns";

const cellSx = { display: "flex", alignItems: "center", height: "100%", px: 1 };

const Empty = () => (
  <Typography typography="s2" color="text.disabled">
    –
  </Typography>
);

export const CommentsCell = ({ data, datasetId }) => {
  const { data: summary } = useDatasetComments(datasetId, { status: "all" });
  const byRow = useMemo(() => commentsByRow(summary), [summary]);
  const text = commentsCellText(byRow[data?.row_id]);
  return (
    <Box sx={cellSx} data-testid="cell-comments">
      {text ? (
        <Typography
          typography="s2"
          color={byRow[data?.row_id]?.open ? "text.primary" : "text.secondary"}
          noWrap
        >
          {text}
        </Typography>
      ) : (
        <Empty />
      )}
    </Box>
  );
};

export const AssignedCell = ({ data, datasetId }) => {
  const { data: read } = useAssignments(datasetId, { status: "open" });
  const byRow = useMemo(() => assigneesByRow(read), [read]);
  const names = byRow[data?.row_id] ?? [];
  return (
    <Box sx={cellSx} data-testid="cell-assigned">
      {names.length ? (
        <Typography typography="s2" noWrap title={names.join(", ")}>
          {names.join(", ")}
        </Typography>
      ) : (
        <Empty />
      )}
    </Box>
  );
};

export const TagsCell = ({ data }) => {
  const { data: reg } = useTags();
  const names = rowTagNames(data);
  const tags = tagRecordsFor(names, reg?.tags);
  return (
    <Box sx={cellSx} data-testid="cell-tags">
      {tags.length ? <TagChips tags={tags} max={3} /> : <Empty />}
    </Box>
  );
};

const cellPropTypes = {
  data: PropTypes.object,
  datasetId: PropTypes.string,
};
CommentsCell.propTypes = cellPropTypes;
AssignedCell.propTypes = cellPropTypes;
TagsCell.propTypes = cellPropTypes;
