import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import SoonChip from "../../components/SoonChip";
import StatusChip from "../runs/StatusChip";
import { RunLetter } from "./RunPills";
import { NONE } from "./compareFormat";
import { METRICS, METRIC_ORDER, evalCell } from "./compareView";

const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

const VERDICT_LABEL = {
  passed: "Passed",
  failed: "Failed",
  flaky: "Passed",
  missing: "Not run",
  unmeasured: "Not measured",
  error: "Error",
  in_progress: "In progress",
};

/** A run's verdict on a scenario, with the trials it rests on. */
export function VerdictChip({ verdict }) {
  const base = VERDICT_LABEL[verdict.status] || "Not measured";
  const label =
    verdict.measured > 1
      ? `${base} ${verdict.passes}/${verdict.measured}`
      : base;
  const status = verdict.status === "missing" ? "cancelled" : verdict.status;
  return <StatusChip status={status} label={label} />;
}
VerdictChip.propTypes = { verdict: PropTypes.object.isRequired };

const PAIR_TIP = {
  new: "Only in the newer run.",
  removed: "Only in the baseline.",
};

/** A row's tag: new or removed, or how its verdict moved against the baseline. */
export function OutcomeChip({ pair }) {
  if (!pair) return null;
  const moved = pair.cls === "paired" && pair.flip.kind !== "same";
  if (pair.cls === "paired" && !moved) return null;
  const bad = pair.cls === "paired" && pair.flip.kind === "regressed";
  const text =
    pair.cls === "paired" ? capitalize(pair.flip.text) : capitalize(pair.cls);
  const tip =
    PAIR_TIP[pair.cls] || `Baseline ${pair.flip.a}, now ${pair.flip.b}.`;
  return (
    <CustomTooltip show arrow size="small" title={tip}>
      <Typography
        component="span"
        noWrap
        sx={{
          px: 0.75,
          py: 0.125,
          borderRadius: "4px",
          flexShrink: 0,
          typography: "s3",
          fontWeight: "fontWeightSemiBold",
          color: bad
            ? "error.main"
            : moved
              ? "text.secondary"
              : "text.subtitle",
          bgcolor: (t) =>
            bad
              ? alpha(t.palette.error.main, 0.1)
              : alpha(t.palette.text.primary, 0.06),
        }}
      >
        {text}
      </Typography>
    </CustomTooltip>
  );
}
OutcomeChip.propTypes = { pair: PropTypes.object };

function ColHead({ children, sx }) {
  return (
    <Typography
      noWrap
      component="div"
      sx={{ fontSize: 13, fontWeight: 500, color: "text.secondary", ...sx }}
    >
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node, sx: PropTypes.object };

const NUMBER_SX = {
  textAlign: "right",
  typography: "s2",
  fontVariantNumeric: "tabular-nums",
};

export function GroupBand({ group }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: 2.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
    >
      <Typography
        noWrap
        sx={{
          fontSize: 13,
          fontWeight: 600,
          color: group.tone === "bad" ? "error.main" : "text.secondary",
        }}
      >
        {group.label}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {group.rows.length}
      </Typography>
      {group.note && (
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
        >
          · {group.note}
        </Typography>
      )}
    </Stack>
  );
}
GroupBand.propTypes = { group: PropTypes.object.isRequired };

/**
 * One row per scenario, every run stacked inside it: its verdict, its
 * numbers and its eval scores. The deciding moment of each call isn't in the
 * API yet, so its column says so.
 */
export default function CompareTable({
  groups,
  runs,
  evals,
  view,
  rowPad,
  onOpen,
}) {
  const metrics = METRIC_ORDER.filter((id) => view.columns[id]);
  const showCost = !!view.columns.cost;
  const metricCount = metrics.length + (showCost ? 1 : 0);
  const metricWidth = metricCount
    ? metricCount * 76 + (metricCount - 1) * 10
    : 0;
  const showScorers = !!view.columns.scorers && evals.length > 0;
  const scorersWidth = showScorers
    ? evals.length * 92 + (evals.length - 1) * 10
    : 0;
  const gridTemplate = [
    "260px",
    "1fr",
    metricWidth && `${metricWidth}px`,
    scorersWidth && `${scorersWidth}px`,
  ]
    .filter(Boolean)
    .join(" ");
  const metricGrid = `repeat(${metricCount}, minmax(56px, 1fr))`;
  const evalGrid = `repeat(${evals.length}, minmax(72px, 1fr))`;

  return (
    <Box>
      <Box
        sx={{
          display: { xs: "none", lg: "grid" },
          gridTemplateColumns: gridTemplate,
          columnGap: 2,
          px: 2.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <ColHead>Scenario</ColHead>
        <Stack direction="row" alignItems="center" spacing={1} minWidth={0}>
          <ColHead>What each run did with it</ColHead>
          <SoonChip
            label="Deciding moment" detail="The turn where each call was won or lost."
            what="The moment that decided each call"
            size="tiny"
          />
        </Stack>
        {metricCount > 0 && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: metricGrid,
              columnGap: 1.25,
            }}
          >
            {metrics.map((id) => (
              <ColHead key={id} sx={{ textAlign: "right" }}>
                {METRICS[id].label}
              </ColHead>
            ))}
            {showCost && (
              <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
                <SoonChip label="Cost" what="Cost per call" size="tiny" />
              </Box>
            )}
          </Box>
        )}
        {showScorers && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: evalGrid,
              columnGap: 1.25,
            }}
          >
            {evals.map((e) => (
              <ColHead key={e.id} sx={{ textAlign: "right" }}>
                {e.name}
              </ColHead>
            ))}
          </Box>
        )}
      </Box>

      {groups.map((group) => (
        <Box key={group.id}>
          {group.label && <GroupBand group={group} />}
          {group.rows.map((row) => (
            <Box
              key={row.id}
              data-testid="compare-row"
              tabIndex={onOpen ? 0 : undefined}
              onClick={onOpen ? () => onOpen(row) : undefined}
              onKeyDown={
                onOpen
                  ? (e) => {
                      if (e.key === "Enter") onOpen(row);
                    }
                  : undefined
              }
              sx={{
                display: "grid",
                gridTemplateColumns: { xs: "1fr", lg: gridTemplate },
                columnGap: 2,
                px: 2.5,
                py: rowPad,
                borderBottom: "1px solid",
                borderColor: "divider",
                cursor: onOpen ? "pointer" : "default",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Box minWidth={0}>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <OutcomeChip pair={row.pair} />
                  <Typography
                    data-testid="compare-row-title"
                    noWrap
                    sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
                  >
                    {row.title}
                  </Typography>
                </Stack>
                {view.rowHeight !== "compact" && row.scenarioDetails && (
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.subtitle",
                      mt: 0.25,
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {row.scenarioDetails}
                  </Typography>
                )}
                {view.rowHeight === "large" && row.persona && (
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
                  >
                    {row.persona}
                  </Typography>
                )}
              </Box>

              <Stack spacing={1.25} sx={{ minWidth: 0 }}>
                {runs.map((run) => (
                  <Stack
                    key={run.id}
                    direction="row"
                    spacing={1.25}
                    alignItems="center"
                  >
                    <RunLetter letter={run.letter} color={run.color} />
                    <VerdictChip verdict={row.verdicts[run.id]} />
                  </Stack>
                ))}
              </Stack>

              {metricCount > 0 && (
                <Stack
                  spacing={1.25}
                  sx={{ display: { xs: "none", lg: "flex" } }}
                >
                  {runs.map((run) => {
                    const trials = row.cells[run.id];
                    return (
                      <Box
                        key={run.id}
                        sx={{
                          display: "grid",
                          gridTemplateColumns: metricGrid,
                          columnGap: 1.25,
                          minHeight: 22,
                          alignItems: "center",
                        }}
                      >
                        {metrics.map((id) => (
                          <Typography key={id} noWrap sx={NUMBER_SX}>
                            {trials.length ? METRICS[id].get(trials) : NONE}
                          </Typography>
                        ))}
                        {showCost && <Box />}
                      </Box>
                    );
                  })}
                </Stack>
              )}

              {showScorers && (
                <Stack
                  spacing={1.25}
                  sx={{ display: { xs: "none", lg: "flex" } }}
                >
                  {runs.map((run) => (
                    <Box
                      key={run.id}
                      sx={{
                        display: "grid",
                        gridTemplateColumns: evalGrid,
                        columnGap: 1.25,
                        minHeight: 22,
                        alignItems: "center",
                      }}
                    >
                      {evals.map((e) => {
                        const cell = evalCell(row.cells[run.id], e.id);
                        return (
                          <Typography
                            key={e.id}
                            sx={{
                              ...NUMBER_SX,
                              fontWeight: cell ? 600 : 400,
                              color: !cell
                                ? "text.disabled"
                                : cell.failed
                                  ? "error.main"
                                  : "text.primary",
                            }}
                          >
                            {cell ? `${Math.round(cell.score * 100)}%` : NONE}
                          </Typography>
                        );
                      })}
                    </Box>
                  ))}
                </Stack>
              )}
            </Box>
          ))}
        </Box>
      ))}
    </Box>
  );
}
CompareTable.propTypes = {
  groups: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  evals: PropTypes.array.isRequired,
  view: PropTypes.object.isRequired,
  rowPad: PropTypes.number,
  onOpen: PropTypes.func,
};
