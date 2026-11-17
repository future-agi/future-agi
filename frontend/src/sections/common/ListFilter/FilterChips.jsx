// The applied conditions as chips (W9-SCOPE §5 A2): `Tag is golden ✕`,
// `Last pass < 80% ✕`, `Clear filters`, and the lines under them (e.g.
// `2 datasets have no finished run and are left out by “Last pass”.`).
// Generic: the page passes its fields and the words for left-out lines.
import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Chip, Stack, Typography } from "@mui/material";
import { chipText, removeAt } from "./listFilterModel";

export default function FilterChips({
  filters,
  fields,
  onChange,
  labelOf,
  notes,
  sx,
}) {
  if (!filters?.length) return null;
  return (
    <Box sx={sx} data-testid="filter-chips">
      <Stack direction="row" alignItems="center" flexWrap="wrap" gap={0.75}>
        {filters.map((c, i) => {
          const text = chipText(c, fields, labelOf);
          return (
            <Chip
              key={`${c.column_id}:${i}`}
              size="small"
              variant="outlined"
              label={text}
              onDelete={() => onChange(removeAt(filters, i))}
              deleteIcon={
                <Box
                  component="span"
                  aria-label={`Remove ${text}`}
                  role="button"
                >
                  ✕
                </Box>
              }
              sx={{
                typography: "s2",
                "& .MuiChip-deleteIcon": { fontSize: 11, px: 0.5 },
              }}
              data-testid="filter-chip"
            />
          );
        })}
        <Button
          size="small"
          color="inherit"
          onClick={() => onChange([])}
          sx={{ typography: "s2", color: "text.secondary" }}
        >
          Clear filters
        </Button>
      </Stack>
      {(notes ?? []).map((line) => (
        <Typography
          key={line}
          typography="s3"
          color="text.secondary"
          sx={{ mt: 0.5 }}
          data-testid="filter-note"
        >
          {line}
        </Typography>
      ))}
    </Box>
  );
}

FilterChips.propTypes = {
  filters: PropTypes.array,
  fields: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  labelOf: PropTypes.func,
  notes: PropTypes.arrayOf(PropTypes.string),
  sx: PropTypes.object,
};
