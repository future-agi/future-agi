import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Stack,
  Tab,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import ScenarioTable from "../workspace/scenarios/ScenarioTable";
import GroupByMenu from "../workspace/scenarios/GroupByMenu";
import CallCompare from "../run/CallCompare";
import {
  EPOCH_SETS,
  RESULT_GROUPINGS,
  RESULT_WORD,
  UNMEASURED_WHY,
  afterTrialsOf,
  epochCompareOf,
  epochEntriesOf,
  inSet,
  setCountsOf,
  sortedGroupsOf,
  tableRowsOf,
} from "./epochResultRows";
import {
  addImprovementScenariosPatch,
  improvementScenariosNotIn,
} from "../_mock/improvementScenarios";

/* Colour only for the two results that change something. */
const RESULT_COLOR = {
  broke: "status.danger.fg",
  fixed: "status.success.fg",
};

/* "3 of 3 trials passed" → "3/3 trials", for the cell. */
const shortTrials = (line) =>
  line ? line.replace(/^(\d+) of (\d+) (trials?) passed$/, "$1/$2 $3") : null;

/**
 * An epoch's Scenarios tab — the environment's own Scenarios table, read
 * only, with one more column: what the winning candidate did to each
 * scenario. Which set a scenario was in is a filter, not a tag on every row.
 * A row opens its two calls side by side: before the change, and with the
 * winning candidate. The scenarios the epoch wrote (an issue's) can join
 * the environment from here, as its regression tests.
 */
export default function EpochScenarioResults({
  env,
  envState,
  record,
  epochName,
  patch,
}) {
  const result = record?.result || null;
  const [query, setQuery] = useState("");
  const [set, setSet] = useState("all");
  /* Flat by default, so the sort reads top to bottom: Broke first. Goal
     and Persona group it the way the Scenarios tab does. */
  const [groupBy, setGroupBy] = useState("none");
  const [dir, setDir] = useState("asc");
  const [openId, setOpenId] = useState(null);
  const [diff, setDiff] = useState(true);

  const entries = useMemo(() => epochEntriesOf(result), [result]);
  /* Scenarios this epoch wrote that the environment doesn't have yet. */
  const newRows = useMemo(
    () => improvementScenariosNotIn(envState, record),
    [envState, record],
  );
  const addNew = () => patch?.(addImprovementScenariosPatch(envState, record));
  const fresh = newRows.filter((r) => r.freshVariation).length;
  const addButton =
    patch && newRows.length > 0 ? (
      <Tooltip
        arrow
        title={`${[newRows.length - fresh ? `${newRows.length - fresh} from the issue's traces` : null, fresh ? `${fresh} fresh ${fresh === 1 ? "variation" : "variations"} from the held-back check` : null].filter(Boolean).join(", ")}. They join ${env?.name || "the environment"} as proposed scenarios, so every later run checks them.`}
      >
        <Button
          size="small"
          variant="outlined"
          onClick={addNew}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {`Add ${newRows.length} ${newRows.length === 1 ? "scenario" : "scenarios"} to the environment`}
        </Button>
      </Tooltip>
    ) : null;
  const counts = useMemo(() => setCountsOf(entries), [entries]);
  /* The environment's scenarios, and the epoch's own (an issue's). */
  const scenarios = useMemo(
    () => [...(envState?.scenarios || []), ...(record?.issueScenarios || [])],
    [envState?.scenarios, record?.issueScenarios],
  );
  const rows = useMemo(
    () =>
      tableRowsOf(
        entries,
        envState?.scenarios || [],
        record?.issueScenarios || [],
      ),
    [entries, envState?.scenarios, record?.issueScenarios],
  );
  /* Before the change: the agent version the epoch started from. */
  const baseVersion =
    (envState?.runs || []).find((r) => r.id === record?.fromRunId)
      ?.agentVersion ||
    record?.baseAgentVersion ||
    null;
  const compare = useMemo(
    () =>
      epochCompareOf(
        env,
        envState,
        record,
        {
          before: `Before the change${baseVersion ? ` · agent ${baseVersion}` : ""}`,
          candidate: (n) =>
            `Candidate ${n} · ${record?.preferredTrial ? "chosen" : "winner"}${baseVersion ? ` · agent ${baseVersion} + change` : ""}`,
        },
        scenarios,
      ),
    [env, envState, record, baseVersion, scenarios],
  );

  const q = query.trim().toLowerCase();
  const shown = rows.filter((r) => {
    if (!inSet(r.epoch, set)) return false;
    if (!q) return true;
    return `${r.name || ""} ${r.summary || ""} ${r.title || ""} ${r.task || ""} ${r.useCase || ""} ${r.persona?.name || ""}`
      .toLowerCase()
      .includes(q);
  });
  const groups = sortedGroupsOf(shown, { groupBy, dir });
  const order = groups.flatMap((g) => g.rows);

  const openRow = openId ? compare?.byId.get(openId) || null : null;
  const openOutcome = rows.find((r) => r.id === openId)?.epoch.outcome;
  const navRows = order.map((r) => compare?.byId.get(r.id)).filter(Boolean);

  const renderResult = (row) => {
    const e = row.epoch;
    const why = UNMEASURED_WHY[e.outcome];
    const after = why ? null : afterTrialsOf(compare, row.id);
    const before = compare?.byId.get(row.id)?.cells?.[0]?.note || null;
    return (
      <Tooltip
        arrow
        enterDelay={300}
        title={
          why
            ? `Not counted: ${why}`
            : e.recheck
              ? "Must-pass: it failed once in the search and passed again on its re-run, so it didn't break"
              : after
                ? `With the candidate: ${after}${before ? ` · before the change: ${before}` : ""}`
                : ""
        }
      >
        <Stack
          direction="row"
          alignItems="baseline"
          spacing={0.75}
          sx={{ minWidth: 0 }}
        >
          <Typography
            noWrap
            sx={{
              typography: "s2",
              fontWeight: 500,
              flexShrink: 0,
              color: RESULT_COLOR[e.outcome] || "text.secondary",
            }}
          >
            {RESULT_WORD[e.outcome] || "Unchanged"}
          </Typography>
          {(why || after) && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
            >
              {why || shortTrials(after)}
            </Typography>
          )}
        </Stack>
      </Tooltip>
    );
  };

  if (!entries.length) {
    return (
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ py: 2 }}>
        <Typography sx={{ typography: "s2", color: "text.tertiary", flex: 1 }}>
          No scenario was measured in this epoch.
        </Typography>
        {addButton}
      </Stack>
    );
  }

  return (
    <Box>
      {/* The Scenarios tab's toolbar: search, the set filter, grouping. */}
      <Stack
        direction="row"
        alignItems="center"
        flexWrap="wrap"
        sx={{ gap: 1, minHeight: 30, mb: 1.5 }}
      >
        <TextField
          size="small"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search scenarios…"
          InputProps={{
            sx: {
              typography: "s2",
              height: 30,
              "& .MuiInputBase-input": { py: 0 },
            },
            startAdornment: (
              <Box
                sx={{
                  pr: 0.75,
                  pl: 0.25,
                  display: "flex",
                  color: "text.tertiary",
                }}
              >
                <Iconify icon="solar:magnifer-linear" width={14} />
              </Box>
            ),
          }}
          sx={{ width: 220, flexShrink: 0 }}
        />
        <SegmentedTabs
          value={set}
          onChange={(_, v) => setSet(v)}
          aria-label="Scenario set"
        >
          <Tab value="all" label={`All ${entries.length}`} />
          {EPOCH_SETS.filter((s) => counts[s.id] > 0).map((s) => (
            <Tab
              key={s.id}
              value={s.id}
              label={
                <Tooltip arrow title={s.tip}>
                  <span>{`${s.label} ${counts[s.id]}`}</span>
                </Tooltip>
              }
            />
          ))}
        </SegmentedTabs>
        <GroupByMenu
          value={groupBy}
          onChange={setGroupBy}
          options={RESULT_GROUPINGS}
        />
        {addButton && (
          <>
            <Box flex={1} />
            {addButton}
          </>
        )}
      </Stack>

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          overflow: "clip",
          "& tbody tr:last-of-type > td": { borderBottom: 0 },
        }}
      >
        {order.length ? (
          <ScenarioTable
            groups={groups}
            env={env}
            envState={envState}
            onOpenRow={compare ? (r) => setOpenId(r.id) : undefined}
            result={{
              label: epochName,
              tip: "What the winning candidate did to this scenario, against the agent before the change",
              w: 190,
              render: renderResult,
              sort: dir,
              onSort: () => setDir((d) => (d === "asc" ? "desc" : "asc")),
            }}
          />
        ) : (
          <Typography sx={{ typography: "s2", color: "text.tertiary", p: 2 }}>
            No scenario matches.
          </Typography>
        )}
      </Box>

      {compare && (
        <CallCompare
          open={!!openRow}
          row={openRow}
          rows={navRows}
          runs={compare.runs}
          env={env}
          envState={envState}
          diff={diff}
          onDiff={setDiff}
          onOpenRow={(r) => setOpenId(r.id)}
          onClose={() => setOpenId(null)}
          /* The result first, then what the two columns are. */
          heading={`${RESULT_WORD[openOutcome] || "Unchanged"} · before the change and with the winning candidate`}
        />
      )}
    </Box>
  );
}

EpochScenarioResults.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  record: PropTypes.object,
  epochName: PropTypes.string,
  /* Saves to the environment — adding the epoch's scenarios needs it. */
  patch: PropTypes.func,
};
