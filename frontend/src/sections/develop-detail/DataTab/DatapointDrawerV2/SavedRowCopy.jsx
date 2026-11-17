import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { useRowCells } from "./itemHooks";

const asText = (value) => {
  if (value == null || value === "") return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

/**
 * The copy of an imported row saved at import: each column's value as the
 * dataset holds it, read-only. Shown where the original trace or session is
 * no longer in Observe.
 */
const SavedRowCopy = ({ datasetId, row }) => {
  const { columns, byName } = useRowCells(datasetId, row);
  if (!columns.length) return null;
  return (
    <Stack
      gap={1}
      data-testid="saved-row-copy"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        py: 1.25,
      }}
    >
      {columns.map((column) => (
        <Box key={column.id} sx={{ minWidth: 0 }}>
          <Typography typography="s3" color="text.secondary">
            {column.name}
          </Typography>
          <Typography
            typography="s2"
            component="div"
            data-testid="saved-row-value"
            sx={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 240,
              overflow: "auto",
            }}
          >
            {asText(byName[column.name])}
          </Typography>
        </Box>
      ))}
    </Stack>
  );
};

SavedRowCopy.propTypes = {
  datasetId: PropTypes.string,
  row: PropTypes.object,
};

export default SavedRowCopy;
