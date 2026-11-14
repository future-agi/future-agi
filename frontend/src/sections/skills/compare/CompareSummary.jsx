import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";


const STATUSES = ["added", "removed", "modified", "unchanged"];

export default function CompareSummary({ summary }) {
  return (
    <Stack
      direction="row"
      useFlexGap
      flexWrap="wrap"
      spacing={2}
      aria-label="File changes"
    >
      {STATUSES.filter((status) => summary[status] > 0).map((status) => (
        <Stack key={status} direction="row" alignItems="center" spacing={0.5}>
          <Typography variant="s1" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {summary[status] ?? 0} {summary[status] === 1 ? "file" : "files"} {status}
          </Typography>
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
