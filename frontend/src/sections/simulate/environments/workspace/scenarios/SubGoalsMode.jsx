import PropTypes from "prop-types";
import { useMemo } from "react";
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import SectionCard from "../../components/SectionCard";
import EmptyState from "../../components/EmptyState";

const MONO = "ui-monospace, Menlo, monospace";
const TEXT_KEYS = ["text", "goal", "description", "label", "what", "name"];

// "Confirms_pickup-and dropoff" and "confirms pickup and dropoff" are one
// sub-goal: case, underscores, dashes and spacing don't change the meaning.
const normalise = (s) =>
  String(s)
    .toLowerCase()
    .replace(/[\s_-]+/g, " ")
    .trim();

// The words a sub-goal reads as, whichever shape it came in.
const subGoalText = (g) => {
  if (g == null) return "";
  if (typeof g === "string") return g;
  const key = TEXT_KEYS.find((k) => typeof g[k] === "string" && g[k].trim());
  return key ? g[key] : g.id != null ? String(g.id) : "";
};

// Every way a sub-goal can be named (its id and its words), normalised, so a
// contract entry and a scenario's own list match on either.
const keysOf = (g) => {
  if (g == null) return [];
  const raw =
    typeof g === "string"
      ? [g]
      : [g.id, ...TEXT_KEYS.map((k) => g[k])].filter(
          (v) => typeof v === "string" || typeof v === "number",
        );
  return raw.map(normalise).filter(Boolean);
};

// A scenario row lists its sub-goals as `subGoals`, or as `subTasks` when it
// comes off the API (sub_goals mapped by harnessDetail/scenarios).
const rowSubGoals = (row) => [
  ...(Array.isArray(row?.subGoals) ? row.subGoals : []),
  ...(Array.isArray(row?.subTasks) ? row.subTasks : []),
];

// How many scenarios name each contract sub-goal: a row counts once however
// many of its entries match.
function usageOf(subGoals, rows) {
  const rowKeys = (rows || []).map(
    (row) => new Set(rowSubGoals(row).flatMap(keysOf)),
  );
  return (subGoals || []).map((goal) => {
    const keys = keysOf(goal);
    return rowKeys.filter((set) => keys.some((k) => set.has(k))).length;
  });
}

// Vel's sub-goals view, inside the Scenarios tab: one entry per sub-goal the
// contract names, with how many scenarios grade with it. The contract carries
// only the sub-goal itself, so his Kind, Tests and Last change columns and the
// edit drawer have nothing real to show yet.
export default function SubGoalsMode({ subGoals, rows }) {
  const list = useMemo(
    () => (Array.isArray(subGoals) ? subGoals : []),
    [subGoals],
  );
  const usage = useMemo(() => usageOf(list, rows), [list, rows]);

  if (list.length === 0) {
    return (
      <SectionCard>
        <EmptyState
          icon="solar:checklist-minimalistic-linear"
          title="No sub-goals in the contract yet."
          body="Sub-goals are the shared, named checks every scenario grades with. They show here once the contract lists them."
        />
      </SectionCard>
    );
  }

  return (
    <SectionCard>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" aria-label="Sub-goals">
          <TableHead>
            <TableRow>
              {["Name", "Used by"].map((h, i) => (
                <TableCell
                  key={h}
                  align={i === 1 ? "right" : "left"}
                  sx={{
                    typography: "s2",
                    fontWeight: 500,
                    color: "text.secondary",
                    bgcolor: "background.paper",
                    backgroundImage: "none",
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    whiteSpace: "nowrap",
                    ...(i === 0 ? { pl: 2.5 } : { pr: 2.5 }),
                  }}
                >
                  {h}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {list.map((goal, i) => {
              const text = subGoalText(goal);
              const id =
                goal && typeof goal === "object" && goal.id != null
                  ? String(goal.id)
                  : null;
              const n = usage[i];
              return (
                <TableRow
                  key={`${id ?? text}-${i}`}
                  sx={{
                    "& .MuiTableCell-body": {
                      borderColor: "divider",
                      verticalAlign: "middle",
                    },
                    "&:last-of-type .MuiTableCell-body": {
                      borderBottom: "none",
                    },
                  }}
                >
                  <TableCell sx={{ pl: 2.5, maxWidth: 520 }}>
                    <Typography
                      noWrap
                      title={text}
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >
                      {text}
                    </Typography>
                    {id && id !== text && (
                      <Typography
                        noWrap
                        sx={{
                          typography: "s3",
                          color: "text.disabled",
                          fontFamily: MONO,
                          fontSize: 12,
                        }}
                      >
                        {id}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{ pr: 2.5, whiteSpace: "nowrap" }}
                  >
                    <Typography
                      sx={{
                        typography: "s2",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {`Used by ${n} `}
                      <Box component="span" sx={{ color: "text.subtitle" }}>
                        {n === 1 ? "scenario" : "scenarios"}
                      </Box>
                    </Typography>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
    </SectionCard>
  );
}

SubGoalsMode.propTypes = {
  // The contract's `sub_goals`: strings, or { id?, text | goal | description }.
  subGoals: PropTypes.arrayOf(
    PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  ),
  // Scenario rows; each may carry `subGoals` or `subTasks`.
  rows: PropTypes.array,
};
