import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";

import FileStatusBadge from "src/sections/skills/components/FileStatusBadge";

const STATUSES = ["added", "removed", "modified", "unchanged"];

export default function CompareSummary({ summary }) {
  return (
    <Stack direction="row" useFlexGap flexWrap="wrap" spacing={2} aria-label="File changes">
      {STATUSES.map((status) => (
        <Stack key={status} direction="row" alignItems="center" spacing={0.5}>
          <Typography variant="s1" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {summary[status] ?? 0}
          </Typography>
          <FileStatusBadge status={status} />
        </Stack>
      ))}
    </Stack>
  );
}

CompareSummary.propTypes = {
  summary: PropTypes.shape({
    added: PropTypes.number,
    removed: PropTypes.number,
    modified: PropTypes.number,
    unchanged: PropTypes.number,
  }).isRequired,
};
