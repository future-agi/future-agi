import PropTypes from "prop-types";
import {
  Box,
  Button,
  Stack,
  TableCell,
  TableRow,
  Typography,
} from "@mui/material";

import { relativeTime } from "src/utils/format-time";

import { VersionBadge } from "../EnvVersionPin";
import { VERSIONS_COPY } from "../workspace.constants";
import { SCENARIOS_COPY } from "./scenarios.constants";
import { BATCH_SHAPE } from "./scenarios.shapes";

const runnableText = (rows, total) => {
  const unique = new Map(rows.map((row) => [row.id, row]));
  if (unique.size < total) return null;
  const blocked = [...unique.values()].filter((row) => row.doorProblem).length;
  return blocked
    ? SCENARIOS_COPY.someCanRun(total - blocked, total)
    : SCENARIOS_COPY.allCanRun(total);
};

export default function ScenarioBatchRow({
  batch,
  rows,
  total,
  colSpan,
  open,
  onToggle,
}) {
  const { version, latest } = batch;
  const who = version.createdBy || SCENARIOS_COPY.batchSystem;
  const built = version.origin === "authoring";
  const headline = built
    ? SCENARIOS_COPY.batchBuilt(who)
    : SCENARIOS_COPY.batchChanged(who);
  const source = built
    ? SCENARIOS_COPY.batchWritten
    : VERSIONS_COPY.origin[version.origin] || null;
  const details = [
    source,
    version.createdAt && relativeTime(version.createdAt),
    version.label && SCENARIOS_COPY.batchVersion(version.label),
    runnableText(rows, total),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <TableRow>
      <TableCell
        colSpan={colSpan}
        sx={{
          borderBottom: "1px solid",
          borderColor: "divider",
          py: 1,
          px: 1.5,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                noWrap
                sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}
              >
                {headline}
              </Typography>
              {latest && <VersionBadge label={SCENARIOS_COPY.latest} />}
            </Stack>
            {details && (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
              >
                {details}
              </Typography>
            )}
          </Box>
          <Button
            size="small"
            variant="text"
            color="inherit"
            aria-expanded={open}
            onClick={onToggle}
            sx={{ flexShrink: 0 }}
          >
            {open
              ? SCENARIOS_COPY.hideBatch(total)
              : SCENARIOS_COPY.showBatch(total)}
          </Button>
        </Stack>
      </TableCell>
    </TableRow>
  );
}

ScenarioBatchRow.propTypes = {
  batch: BATCH_SHAPE.isRequired,
  rows: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, doorProblem: PropTypes.object }),
  ).isRequired,
  total: PropTypes.number.isRequired,
  colSpan: PropTypes.number.isRequired,
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
};
