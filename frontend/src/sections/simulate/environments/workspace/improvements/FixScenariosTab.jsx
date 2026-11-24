import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TableSortLabel,
  Tooltip,
  Typography,
} from "@mui/material";

import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { useFixScenarioRows } from "src/api/simulate-environments/fixRuns";

import ScenarioCompare from "./ScenarioCompare";
import { winnerOf } from "./fixRunView";
import { scenarioOutcomes, scenarioSetOf } from "./scenarioOutcomes";

const OUTCOME = {
  fixed: "Fixed",
  broke: "Broke",
  "still-failing": "Still failing",
  same: "Unchanged",
};

// Colour only for the two results that change something.
const OUTCOME_COLOR = {
  broke: "status.danger.fg",
  fixed: "status.success.fg",
};

// The chips a scenario filters by: every scenario is in exactly one, so All is
// always their sum. Must-pass has no chip: the read counts its scenarios but
// does not list them, so the rest are Other, never must-pass by elimination.
const SETS = [
  {
    id: "target",
    label: "Target",
    tip: "The failing scenarios the candidates were tuned on",
  },
  {
    id: "held",
    label: "Held back",
    tip: "Never shown to the candidates: they check the change holds",
  },
  {
    id: "other",
    label: "Other",
    tip: "Not targets or held back: only checked for whether the change broke them",
  },
];
const chipOf = (row) =>
  row.set === "target" || row.set === "held" ? row.set : "other";
// The Set column: a protected case is must-pass whatever else holds, so its
// row says so; any other Other row says nothing.
const SET_LABEL = {
  target: "Target",
  held: "Held back",
  protected: "Must-pass",
};

const trialsText = ({ passed, of }) =>
  `${passed}/${of} ${of === 1 ? "trial" : "trials"}`;

// The design's Scenarios tab (EpochScenarioResults), fed from the check runs:
// what the chosen candidate did to each scenario against today's version, Broke
// first. A row opens its calls before the change beside the candidate's.
export default function FixScenariosTab({ fix, candidateId }) {
  const picked = candidateId || winnerOf(fix)?.id;
  const candidate = (fix.candidates || []).find((c) => c.id === picked);
  const { before, after, isLoading, isError } = useFixScenarioRows(fix, picked);
  const [set, setSet] = useState("all");
  const [dir, setDir] = useState("asc");
  const [openKey, setOpenKey] = useState(null);

  if (isLoading)
    return <ContentSkeleton rows={5} label="Loading the results" />;
  if (isError) {
    return (
      <ErrorState
        title="Could not load the scenario results"
        detail="The calls of this epoch's check runs did not load. Reload the page to try again."
      />
    );
  }

  const rows = scenarioOutcomes(before, after).map((r) => ({
    ...r,
    set: scenarioSetOf(fix.request, r.key),
  }));
  if (!rows.length) {
    return (
      <EmptyState
        variant="no-results"
        icon="solar:list-check-linear"
        title="No scenario to compare yet"
        description="A scenario shows here once the current version and this candidate both have a measured call on it."
      />
    );
  }
  const counts = Object.fromEntries(
    SETS.map((s) => [s.id, rows.filter((r) => chipOf(r) === s.id).length]),
  );
  // A set left with no scenario (another candidate reviewed) has no tab, so the
  // table falls back to All rather than read empty.
  const chosen = counts[set] ? set : "all";
  const shown = rows.filter((r) => chosen === "all" || chipOf(r) === chosen);
  // Sorted Broke first; the other way puts it last.
  if (dir === "desc") shown.reverse();
  const name = candidate ? `Candidate ${candidate.attempt}` : "the candidate";
  const opened = rows.find((r) => r.key === openKey) || null;

  return (
    <Box>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <SegmentedTabs
          value={chosen}
          onChange={(_, v) => setSet(v)}
          aria-label="Scenario set"
        >
          <Tab value="all" label={`All ${rows.length}`} />
          {SETS.filter((s) => counts[s.id] > 0).map((s) => (
            <Tab
              key={s.id}
              value={s.id}
              label={
                <Tooltip arrow describeChild title={s.tip}>
                  <span>{`${s.label} ${counts[s.id]}`}</span>
                </Tooltip>
              }
            />
          ))}
        </SegmentedTabs>
      </Stack>
      <Table size="small" aria-label="Scenarios">
        <TableHead>
          <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
            <TableCell>Scenario</TableCell>
            <TableCell sx={{ width: 120 }}>Set</TableCell>
            <TableCell sx={{ width: 200 }} sortDirection={dir}>
              <Tooltip
                arrow
                describeChild
                title={`What ${name} did to this scenario, against the agent before the change`}
              >
                <TableSortLabel
                  active
                  direction={dir}
                  onClick={() => setDir((d) => (d === "asc" ? "desc" : "asc"))}
                >
                  Result
                </TableSortLabel>
              </Tooltip>
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {shown.map((r) => (
            <TableRow
              key={r.key}
              hover
              tabIndex={0}
              data-testid={`scenario-${r.key}`}
              onClick={() => setOpenKey(r.key)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOpenKey(r.key);
                }
              }}
              sx={{ height: ROW_HEIGHT.default, cursor: "pointer" }}
            >
              <TableCell>
                <Typography sx={{ typography: "s2" }}>{r.name}</Typography>
              </TableCell>
              <TableCell>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {SET_LABEL[r.set] || ""}
                </Typography>
              </TableCell>
              <TableCell>
                <Stack direction="row" alignItems="baseline" spacing={0.75}>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 500,
                      color: OUTCOME_COLOR[r.outcome] || "text.secondary",
                    }}
                  >
                    {OUTCOME[r.outcome]}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {trialsText(r.afterTrials)}
                  </Typography>
                </Stack>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <ScenarioCompare
        row={opened}
        heading={
          opened
            ? `${OUTCOME[opened.outcome]} · before the change and with ${name}`
            : ""
        }
        afterTitle={`With ${name}`}
        envId={fix.environment_id}
        onClose={() => setOpenKey(null)}
      />
    </Box>
  );
}

FixScenariosTab.propTypes = {
  fix: PropTypes.object.isRequired,
  candidateId: PropTypes.string,
};
