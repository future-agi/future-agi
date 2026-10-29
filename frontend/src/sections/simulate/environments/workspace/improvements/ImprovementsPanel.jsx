import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Link, Typography } from "@mui/material";

import { DataTable } from "src/components/data-table";
import { paths } from "src/routes/paths";
import { fDateTime } from "src/utils/format-time";
import { useEnvironmentFixRuns } from "src/api/simulate-environments/fixRuns";

import FixStatusChip from "./FixStatusChip";
import {
  fixName,
  formatCredits,
  isShipped,
  listResult,
  winnerOf,
} from "./fixRunView";

const ROW_HEIGHT = 44;
const HEADER_HEIGHT = 40;

const dash = (
  <Typography sx={{ typography: "s3", color: "text.disabled" }}>—</Typography>
);

function HandOff({ fix }) {
  const winner = winnerOf(fix);
  if (winner?.pull_request_url) {
    return (
      <Link
        href={winner.pull_request_url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(e) => e.stopPropagation()}
        sx={{ typography: "s2" }}
      >
        Pull request
      </Link>
    );
  }
  if (isShipped(fix, winner)) {
    return <Typography sx={{ typography: "s2" }}>Default</Typography>;
  }
  return dash;
}
HandOff.propTypes = { fix: PropTypes.object.isRequired };

const columns = [
  {
    id: "name",
    header: "Name",
    meta: { flex: 1.6 },
    minSize: 260,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => (
      <Box minWidth={0}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
          {fixName(fix)}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {`${fix.agent.name} · v${fix.agent.version_number}`}
        </Typography>
      </Box>
    ),
  },
  {
    id: "status",
    header: "Status",
    size: 190,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => <FixStatusChip state={fix.state} />,
  },
  {
    id: "result",
    header: "Result",
    size: 190,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => {
      const result = listResult(fix);
      if (!result) return dash;
      return (
        <Box minWidth={0}>
          <Typography
            noWrap
            sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
          >
            {result.text}
          </Typography>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: result.broke ? "status.danger.fg" : "text.subtitle",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {result.note}
          </Typography>
        </Box>
      );
    },
  },
  {
    id: "output",
    header: "Hand-off",
    size: 150,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => <HandOff fix={fix} />,
  },
  {
    id: "cost",
    header: "Cost",
    size: 130,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => (
      <Typography
        noWrap
        sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
      >
        {formatCredits(fix.spent_credits)}
      </Typography>
    ),
  },
  {
    id: "createdAt",
    header: "Created",
    size: 170,
    enableSorting: false,
    cell: ({ row: { original: fix } }) => (
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
        {fDateTime(fix.created_at)}
      </Typography>
    ),
  },
];

export default function ImprovementsPanel({ env }) {
  const navigate = useNavigate();
  const { fixes } = useEnvironmentFixRuns(env.id);
  return (
    <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
      <DataTable
        columns={columns}
        data={fixes}
        rowCount={fixes.length}
        getRowId={(fix) => fix.id}
        onRowClick={(fix) =>
          navigate(
            paths.dashboard.simulate.environments.improvement(env.id, fix.id),
          )
        }
        rowHeight={ROW_HEIGHT}
        headerHeight={HEADER_HEIGHT}
        emptyMessage="Nothing has been improved in this environment yet. Debug failures on a finished run starts one."
      />
    </Box>
  );
}

ImprovementsPanel.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
};
