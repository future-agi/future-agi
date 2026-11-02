import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SoonChip from "../../components/SoonChip";
import { RunLetter } from "./RunPills";
import { GroupBand, OutcomeChip, VerdictChip } from "./CompareTable";
import { METRICS, METRIC_ORDER, evalCell } from "./compareView";
import { NONE } from "./compareFormat";

function Stat({ label, value }) {
  return (
    <Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightSemiBold",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}
Stat.propTypes = { label: PropTypes.string, value: PropTypes.node };

function Cell({ trials, verdict, evals, view, onOpen }) {
  const metrics = METRIC_ORDER.filter((id) => view.columns[id]);
  return (
    <Box
      onClick={onOpen}
      sx={{
        px: 2,
        py: 2,
        borderLeft: "1px solid",
        borderColor: "divider",
        cursor: onOpen ? "pointer" : "default",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Box sx={{ mb: 1.5, display: "flex" }}>
        <VerdictChip verdict={verdict} />
      </Box>
      <Stack
        direction="row"
        spacing={2}
        flexWrap="wrap"
        rowGap={1}
        sx={{ mb: view.columns.scorers ? 1.5 : 0 }}
      >
        {metrics.map((id) => (
          <Stat
            key={id}
            label={METRICS[id].label}
            value={trials.length ? METRICS[id].get(trials) : NONE}
          />
        ))}
        {view.columns.cost && (
          <Stat
            label="Cost"
            value={<SoonChip label="Cost" what="Cost per call" size="tiny" />}
          />
        )}
      </Stack>
      {view.columns.scorers && evals.length > 0 && (
        <Box
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          <Typography
            sx={{
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              color: "text.subtitle",
              px: 1.25,
              pt: 0.875,
            }}
          >
            Evaluations
          </Typography>
          <Stack sx={{ px: 1.25, py: 0.75 }} spacing={0.375}>
            {evals.map((e) => {
              const cell = evalCell(trials, e.id);
              return (
                <Stack
                  key={e.id}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                >
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {e.name}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: "fontWeightSemiBold",
                      fontVariantNumeric: "tabular-nums",
                      color: !cell
                        ? "text.disabled"
                        : cell.failed
                          ? "error.main"
                          : "text.primary",
                    }}
                  >
                    {cell ? `${Math.round(cell.score * 100)}%` : NONE}
                  </Typography>
                </Stack>
              );
            })}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
Cell.propTypes = {
  trials: PropTypes.array.isRequired,
  verdict: PropTypes.object.isRequired,
  evals: PropTypes.array.isRequired,
  view: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
};

/**
 * One column per run, read across: how each version handled the same
 * scenario. The scenario spans the row above its cells.
 */
export default function CompareGrid({
  groups,
  runs,
  runStats,
  evals,
  view,
  onOpen,
}) {
  const columns = `220px repeat(${runs.length}, 1fr)`;
  return (
    <Box data-testid="compare-grid" sx={{ overflowX: "auto" }}>
      <Box sx={{ minWidth: 260 + runs.length * 300 }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: columns,
            borderBottom: "1px solid",
            borderColor: "divider",
            position: "sticky",
            top: 0,
            zIndex: 2,
            bgcolor: "background.paper",
          }}
        >
          <Typography
            sx={{
              px: 2.5,
              py: 1.5,
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              color: "text.subtitle",
            }}
          >
            Scenario
          </Typography>
          {runs.map((run, i) => (
            <Stack
              key={run.id}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2,
                py: 1.5,
                borderLeft: "1px solid",
                borderColor: "divider",
              }}
            >
              <RunLetter letter={run.letter} color={run.color} />
              <Typography
                noWrap
                sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
              >
                {run.label}
              </Typography>
              {i === 0 && (
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  baseline
                </Typography>
              )}
              <Box flex={1} />
              <Typography
                sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
              >
                {runStats[run.id]?.passRate == null
                  ? NONE
                  : `${runStats[run.id].passRate}%`}
              </Typography>
            </Stack>
          ))}
        </Box>

        {groups.map((group) => (
          <Box key={group.id}>
            {group.label && <GroupBand group={group} />}
            {group.rows.map((row) => (
              <Box key={row.id} data-testid="grid-row">
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  onClick={onOpen ? () => onOpen(row) : undefined}
                  sx={{
                    px: 2.5,
                    py: 1.25,
                    cursor: onOpen ? "pointer" : "default",
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Iconify
                    icon="solar:layers-minimalistic-linear"
                    width={14}
                    sx={{ color: "text.subtitle", flexShrink: 0 }}
                  />
                  <OutcomeChip pair={row.pair} />
                  <Typography
                    noWrap
                    sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
                  >
                    {row.title}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: "text.subtitle",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {row.goal}
                  </Typography>
                </Stack>
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: columns,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Box sx={{ px: 2.5, py: 2 }}>
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {row.persona || NONE}
                    </Typography>
                  </Box>
                  {runs.map((run) => (
                    <Cell
                      key={run.id}
                      trials={row.cells[run.id]}
                      verdict={row.verdicts[run.id]}
                      evals={evals}
                      view={view}
                      onOpen={onOpen ? () => onOpen(row) : undefined}
                    />
                  ))}
                </Box>
              </Box>
            ))}
          </Box>
        ))}
      </Box>
    </Box>
  );
}
CompareGrid.propTypes = {
  groups: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  runStats: PropTypes.object.isRequired,
  evals: PropTypes.array.isRequired,
  view: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
};
