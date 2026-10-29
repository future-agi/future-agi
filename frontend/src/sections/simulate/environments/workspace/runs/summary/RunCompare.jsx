import PropTypes from "prop-types";
import {
  Box,
  Chip,
  CircularProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { useCompareRuns } from "src/api/simulate-environments/compareRuns";
import SideDrawer from "../../../components/SideDrawer";
import { COMPARE_COPY as COPY } from "../../workspace.constants";
import { COLORS, format, number } from "../detail/analytics/DashboardCharts";

// On a dot beside the verdict only: as text these fail contrast.
const VERDICT_COLORS = {
  improved: COLORS[1],
  regressed: COLORS[2],
  no_meaningful_change: COLORS[5],
  inconclusive: COLORS[3],
};
// What needs a look first: changes, then the unsure, then what can't be judged.
const ORDER = {
  regressed: 0,
  improved: 1,
  inconclusive: 2,
  criteria_changed: 3,
  no_meaningful_change: 4,
  new: 5,
  not_applicable: 6,
};
const tableSx = {
  "& th": { fontSize: 10, textTransform: "uppercase", color: "text.secondary" },
  "& td": { fontSize: 12, verticalAlign: "top" },
  "& tr:last-child td": { borderBottom: 0 },
};

const signed = (value) =>
  value == null ? "-" : `${value > 0 ? "+" : ""}${number(value)}`;

const versionLine = (versions) =>
  Object.entries(COPY.pieces)
    .map(([piece, name]) =>
      versions?.[piece]?.label ? `${name} ${versions[piece].label}` : null,
    )
    .filter(Boolean)
    .join(" · ");

const rank = (pair) =>
  ORDER[pair.band === "paired" ? pair.verdict : pair.band] ?? ORDER.new;

function Side({ side }) {
  if (!side) return "-";
  return (
    <>
      {format(side.pass_rate, "percent")}
      <Typography sx={{ fontSize: 10, color: "text.secondary" }}>
        {COPY.passed(side.passed, side.trials, side.undecided)}
      </Typography>
    </>
  );
}
Side.propTypes = { side: PropTypes.object };

function Verdict({ pair }) {
  const verdict = pair.verdict ? (
    <Stack direction="row" spacing={0.5} alignItems="center">
      <Box
        component="span"
        aria-hidden
        sx={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          flexShrink: 0,
          bgcolor: VERDICT_COLORS[pair.verdict],
        }}
      />
      <Typography sx={{ fontSize: 12, fontWeight: 600 }}>
        {COPY.verdicts[pair.verdict]}
      </Typography>
    </Stack>
  ) : null;
  const interval = pair.interval
    ? COPY.interval(signed(pair.interval.low), signed(pair.interval.high))
    : null;
  let hint = null;
  if (pair.band === "new")
    hint = pair.current ? COPY.newHint : COPY.newInComparedHint;
  if (pair.band === "not_applicable")
    hint =
      COPY.removed(pair.reason, pair.removed_in?.label) ||
      (pair.current ? COPY.notRunInComparedHint : COPY.notApplicableHint);
  if (pair.band === "criteria_changed")
    hint = COPY.criteriaHint(pair.changed_sub_goals);
  return (
    <Stack spacing={0.25} alignItems="flex-start">
      {pair.band !== "paired" && (
        <Chip size="small" label={COPY.bands[pair.band]} />
      )}
      {verdict}
      {[interval, hint].filter(Boolean).map((text) => (
        <Typography key={text} sx={{ fontSize: 10, color: "text.secondary" }}>
          {text}
        </Typography>
      ))}
    </Stack>
  );
}
Verdict.propTypes = { pair: PropTypes.object.isRequired };

// Two finished runs picked on the Runs tab, side by side: `current` is the
// newer run, `against` the older.
export default function RunCompare({ open, onClose, env, current, against }) {
  const { data, isPending, isError } = useCompareRuns(
    env?.id,
    current?.executionId,
    against?.executionId,
    { enabled: open },
  );
  const pairs = [...(data?.pairs ?? [])].sort((a, b) => rank(a) - rank(b));
  const summary = data?.summary;
  const counts = summary
    ? Object.entries(COPY.summary)
        .filter(([key]) => summary[key])
        .map(([key, label]) => `${summary[key]} ${label}`)
    : [];

  let body;
  if (isPending) {
    body = (
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
        <CircularProgress size={14} />
        <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
          {COPY.loading}
        </Typography>
      </Stack>
    );
  } else if (isError) {
    body = (
      <Typography sx={{ mt: 0.75, fontSize: 12, color: "text.secondary" }}>
        {COPY.error}
      </Typography>
    );
  } else {
    body = (
      <>
        <Typography sx={{ mt: 0.75, fontSize: 11, color: "text.secondary" }}>
          {COPY.versions(current?.label, versionLine(data.versions?.current))}
          {" · "}
          {COPY.versions(against?.label, versionLine(data.versions?.against))}
        </Typography>
        {counts.length > 0 && (
          <Typography sx={{ mt: 0.5, fontSize: 12 }}>
            {counts.join(" · ")}
          </Typography>
        )}
        <Box sx={{ overflowX: "auto", mt: 1 }}>
          <Table size="small" sx={tableSx}>
            <TableHead>
              <TableRow>
                <TableCell>{COPY.columns.scenario}</TableCell>
                <TableCell align="right">
                  {current?.label ?? COPY.columns.current}
                </TableCell>
                <TableCell align="right">
                  {against?.label ?? COPY.columns.against}
                </TableCell>
                <TableCell align="right">{COPY.columns.change}</TableCell>
                <TableCell>{COPY.columns.verdict}</TableCell>
                <TableCell align="right">{COPY.columns.evals}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {pairs.map((pair) => (
                <TableRow key={pair.scenario_key}>
                  <TableCell>{pair.scenario}</TableCell>
                  <TableCell align="right">
                    <Side side={pair.current} />
                  </TableCell>
                  <TableCell align="right">
                    <Side side={pair.against} />
                  </TableCell>
                  <TableCell align="right">
                    {pair.difference == null
                      ? "-"
                      : `${signed(pair.difference)} pts`}
                  </TableCell>
                  <TableCell>
                    <Verdict pair={pair} />
                  </TableCell>
                  <TableCell align="right">
                    {format(pair.current?.eval_pass_rate, "percent")} /{" "}
                    {format(pair.against?.eval_pass_rate, "percent")}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      </>
    );
  }

  return (
    <SideDrawer open={open} onClose={onClose} width={860}>
      <Box component="section" aria-label={COPY.heading} sx={{ p: 3 }}>
        <Typography
          component="h2"
          sx={{ typography: "m2", fontWeight: "fontWeightSemiBold", pr: 4 }}
        >
          {COPY.heading}
        </Typography>
        <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
          {COPY.subtitle(summary?.margin ?? 10)}
        </Typography>
        {body}
      </Box>
    </SideDrawer>
  );
}
RunCompare.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  current: PropTypes.shape({
    executionId: PropTypes.string,
    label: PropTypes.string,
  }),
  against: PropTypes.shape({
    executionId: PropTypes.string,
    label: PropTypes.string,
  }),
};
