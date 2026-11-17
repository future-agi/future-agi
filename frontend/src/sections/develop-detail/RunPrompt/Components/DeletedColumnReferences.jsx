import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";

// A saved prompt can still use a column that was deleted after it was saved.
// The prompt keeps that column as {{<id>}}; this names it so the person can
// find it and remove it. A run of such a prompt fills each cell with a
// sentence that says why it cannot run.
export default function DeletedColumnReferences({ references }) {
  if (!references?.length) return null;
  return (
    <Box
      role="status"
      aria-label="Deleted columns in this prompt"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        padding: (theme) => theme.spacing(1.5, 2),
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      <Typography typography="s2" color="text.primary">
        This prompt uses a column that was deleted. Remove it from the prompt to
        run this column.
      </Typography>
      <Stack gap={0.75}>
        {references.map((reference) => (
          <Stack
            key={reference.id}
            direction="row"
            alignItems="center"
            gap={1}
            flexWrap="wrap"
          >
            <Chip
              size="small"
              variant="outlined"
              label={
                reference.name
                  ? `Deleted column: ${reference.name}`
                  : "Deleted column"
              }
              sx={{ color: "status.danger.fg", borderColor: "status.danger.solid" }}
            />
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
            >
              {`{{${reference.id}}}`}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

DeletedColumnReferences.propTypes = {
  references: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string }),
  ),
};
