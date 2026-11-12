import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import EmptyState from "src/components/empty-state";
import SkillsNotice from "src/sections/agent-playground/connected/detail/SkillsNotice";
import { INTEGRATE_TRACING } from "src/sections/agent-playground/connected/detail/skillsCopy";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";

const observedText = (item, state) => {
  if (item.kind === "context") return "Always loaded";
  if (state !== "instrumented" || item.observed == null) return "Not tracked";
  return item.observed ? "Observed" : "Not observed";
};

export default function CallSkillsPanel({ skills }) {
  if (skills.state === "not_scanned" || !skills.items.length)
    return (
      <EmptyState
        icon="solar:lightbulb-bolt-linear"
        title="No skills for this call"
        description="This call's agent version has no skills read from its code."
      />
    );
  return (
    <Stack spacing={1.5} sx={{ px: 1.5, py: 1 }}>
      {skills.state === "not_instrumented" && (
        <SkillsNotice icon="solar:info-circle-linear">
          {INTEGRATE_TRACING}
        </SkillsNotice>
      )}
      {skills.state === "unavailable" && (
        <SkillsNotice icon="solar:clock-circle-linear">
          Skill usage can&rsquo;t be read right now. Try again shortly.
        </SkillsNotice>
      )}
      <Table size="small">
        <TableHead>
          <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
            <TableCell>Skill</TableCell>
            <TableCell>Version</TableCell>
            <TableCell>Path</TableCell>
            <TableCell>In this call</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {skills.items.map((item) => (
            <TableRow key={item.path} hover sx={{ height: ROW_HEIGHT.default }}>
              <TableCell>
                <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                  {item.name}
                </Typography>
              </TableCell>
              <TableCell>{`v${item.version_number}`}</TableCell>
              <TableCell>
                <Box
                  component="span"
                  sx={{ fontFamily: "monospace", color: "text.secondary" }}
                >
                  {item.path}
                </Box>
              </TableCell>
              <TableCell
                sx={{
                  color: item.observed ? "text.primary" : "text.secondary",
                }}
              >
                {observedText(item, skills.state)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Stack>
  );
}

CallSkillsPanel.propTypes = {
  skills: PropTypes.shape({
    state: PropTypes.string.isRequired,
    items: PropTypes.arrayOf(
      PropTypes.shape({
        name: PropTypes.string.isRequired,
        path: PropTypes.string.isRequired,
        kind: PropTypes.string.isRequired,
        version_number: PropTypes.number.isRequired,
        observed: PropTypes.bool,
      }),
    ).isRequired,
  }).isRequired,
};
