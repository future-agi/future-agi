import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { GATEWAY_MONO } from "../../utils/fonts";
import { revisionChanges } from "./revisionDiff";

function Value({ value }) {
  return (
    <Typography
      component="pre"
      variant="body2"
      sx={{
        m: 0,
        fontFamily: GATEWAY_MONO,
        whiteSpace: "pre-wrap",
        overflowWrap: "anywhere",
      }}
    >
      {value === undefined
        ? "Not present"
        : typeof value === "string"
          ? value
          : JSON.stringify(value, null, 2)}
    </Typography>
  );
}
Value.propTypes = { value: PropTypes.any };

export default function RevisionDiff({ row }) {
  const changes = revisionChanges(row);
  return (
    <Stack spacing={1.5}>
      {changes.map((change, index) => (
        <Box
          key={`${index}:${change.path}`}
          sx={{ border: 1, borderColor: "divider", p: 2 }}
        >
          <Typography
            variant="subtitle2"
            sx={{ overflowWrap: "anywhere", mb: 1 }}
          >
            {change.path}
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
              gap: 2,
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary">
                Before
              </Typography>
              <Value value={change.before} />
            </Box>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary">
                After
              </Typography>
              <Value value={change.after} />
            </Box>
          </Box>
        </Box>
      ))}
      {!changes.length && (
        <Typography variant="body2">
          No field-level changes reported. Review the complete definitions
          below.
        </Typography>
      )}
      <Box component="details">
        <Typography component="summary" variant="body2">
          Complete tool definitions
        </Typography>
        <Typography variant="subtitle2">Accepted definition</Typography>
        <Value value={row.previous_definition ?? undefined} />
        <Typography variant="subtitle2">Proposed definition</Typography>
        <Value value={row.definition} />
      </Box>
    </Stack>
  );
}
RevisionDiff.propTypes = { row: PropTypes.object.isRequired };
