import PropTypes from "prop-types";
import { Fragment, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  Box,
  ButtonBase,
  Link,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";

import SideDrawer from "../../components/SideDrawer";

import FixChangesTab from "./FixChangesTab";
import { candidateGroups } from "./fixRunView";

const COLUMNS = 6;
const num = { fontVariantNumeric: "tabular-nums" };
const quiet = { typography: "s2", color: "text.tertiary" };

function TargetCell({ target }) {
  if (!target) return <Typography sx={quiet}>—</Typography>;
  const { score, delta } = target;
  return (
    <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 0.75 }}>
      <Typography noWrap sx={{ typography: "s2", ...num }}>
        {`${score}%`}
      </Typography>
      <Typography
        noWrap
        sx={{
          typography: "s3",
          fontWeight: 500,
          width: 52,
          ...num,
          color:
            delta > 0
              ? "status.success.fg"
              : delta < 0
                ? "status.danger.fg"
                : "text.tertiary",
        }}
      >
        {delta === 0
          ? "same"
          : `${delta > 0 ? "↑" : "↓"} ${Math.abs(delta)} pts`}
      </Typography>
    </Box>
  );
}
TargetCell.propTypes = { target: PropTypes.object };

function ResultCell({ row, winner }) {
  if (winner) {
    return <StatusBadge status="passed" role="success" label="Winner" />;
  }
  return (
    <Tooltip arrow title={row.result} enterDelay={300}>
      <Typography
        noWrap
        sx={{
          typography: "s2",
          color: row.broke ? "status.danger.fg" : "text.secondary",
        }}
      >
        {row.result}
      </Typography>
    </Tooltip>
  );
}
ResultCell.propTypes = {
  row: PropTypes.object.isRequired,
  winner: PropTypes.bool,
};

// The design's Candidates tab (EpochCandidatesTable): every candidate the epoch
// tried, grouped by where it stopped. Each group folds; the ones worth reading
// start open. A candidate's run opens on its page; its change opens beside this one.
export default function CandidatesTable({ fix }) {
  const location = useLocation();
  const groups = candidateGroups(fix);
  const [open, setOpen] = useState(() =>
    Object.fromEntries(groups.map((g) => [g.id, g.open])),
  );
  const [seeing, setSeeing] = useState(null);
  const toggle = (id) => setOpen((o) => ({ ...o, [id]: !o[id] }));
  const seen = (fix.candidates || []).find((c) => c.id === seeing);
  return (
    <>
      <Table size="small" aria-label="Candidates">
        <TableHead>
          <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
            <TableCell>Candidate</TableCell>
            <TableCell>What it tried</TableCell>
            <Tooltip
              arrow
              describeChild
              title="Pass rate on the scenarios the epoch tuned on, against the agent before the change. Sorted high to low in each group."
            >
              <TableCell align="right">Target scenarios</TableCell>
            </Tooltip>
            <Tooltip
              arrow
              describeChild
              title="Scenarios kept out of the search. Only finalists run on them."
            >
              <TableCell>Held back</TableCell>
            </Tooltip>
            <TableCell>Result</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {groups.map((g) => (
            <Fragment key={g.id}>
              <TableRow sx={{ height: ROW_HEIGHT.default }}>
                <TableCell colSpan={COLUMNS} sx={{ p: 0 }}>
                  <ButtonBase
                    data-testid={`candidate-group-${g.id}`}
                    aria-expanded={!!open[g.id]}
                    onClick={() => toggle(g.id)}
                    sx={{
                      width: 1,
                      height: ROW_HEIGHT.default,
                      px: 2,
                      gap: 0.75,
                      justifyContent: "flex-start",
                      bgcolor: "background.neutral",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Iconify
                      icon={
                        open[g.id]
                          ? "solar:alt-arrow-down-linear"
                          : "solar:alt-arrow-right-linear"
                      }
                      width={12}
                      sx={{ color: "text.tertiary", flexShrink: 0 }}
                    />
                    <Typography
                      sx={{ typography: "s2", fontWeight: 500, flex: 1 }}
                      textAlign="left"
                    >
                      {g.label}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary", ...num }}
                    >
                      {g.rows.length}
                    </Typography>
                  </ButtonBase>
                </TableCell>
              </TableRow>
              {open[g.id] &&
                g.rows.map((row) => (
                  <TableRow
                    key={row.id}
                    hover
                    data-testid={`candidate-${row.attempt}`}
                    sx={{ height: ROW_HEIGHT.default }}
                  >
                    <TableCell>
                      <Typography
                        noWrap
                        sx={{ typography: "s2", fontWeight: 500 }}
                      >
                        {`Candidate ${row.attempt}`}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ maxWidth: 360 }}>
                      <Tooltip arrow title={row.tried} enterDelay={400}>
                        <Typography
                          noWrap
                          sx={{ typography: "s2", color: "text.secondary" }}
                        >
                          {row.tried || "—"}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell align="right">
                      <TargetCell target={row.target} />
                    </TableCell>
                    <TableCell>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s2",
                          ...num,
                          color: row.held ? "text.secondary" : "text.tertiary",
                        }}
                      >
                        {row.held || "Not run"}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ maxWidth: 280 }}>
                      <ResultCell row={row} winner={g.id === "winner"} />
                    </TableCell>
                    <TableCell align="right">
                      <Box
                        sx={{
                          display: "flex",
                          justifyContent: "flex-end",
                          gap: 1.5,
                        }}
                      >
                        {row.run && (
                          <Link
                            component={RouterLink}
                            href={paths.dashboard.simulate.environments.execution(
                              fix.environment_id,
                              row.run.run_id,
                              row.run.test_execution_id,
                            )}
                            state={{ backTo: location.pathname }}
                            underline="hover"
                            sx={{ typography: "s2", whiteSpace: "nowrap" }}
                          >
                            Open run
                          </Link>
                        )}
                        <Link
                          component="button"
                          type="button"
                          underline="hover"
                          onClick={() => setSeeing(row.id)}
                          sx={{ typography: "s2", whiteSpace: "nowrap" }}
                        >
                          See its change
                        </Link>
                      </Box>
                    </TableCell>
                  </TableRow>
                ))}
            </Fragment>
          ))}
        </TableBody>
      </Table>
      <SideDrawer open={!!seen} onClose={() => setSeeing(null)} width={720}>
        {seen && (
          <Box data-testid="candidate-change" sx={{ p: 2.5, pr: 5 }}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {`Candidate ${seen.attempt}'s change`}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mb: 2 }}
            >
              {seen.summary || "Against the version the epoch started from"}
            </Typography>
            <FixChangesTab fix={fix} candidateId={seen.id} />
          </Box>
        )}
      </SideDrawer>
    </>
  );
}

CandidatesTable.propTypes = { fix: PropTypes.object.isRequired };
