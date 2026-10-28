import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  CircularProgress,
  Collapse,
  IconButton,
  TextField,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { fToNow } from "src/utils/format-time";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";
import { useRunCalls } from "src/api/simulate-environments/runCalls";
import EmptyState from "../../../components/EmptyState";
import SoonChip from "../../../components/SoonChip";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";

// The calls list caps a page at 500 (RunCallsV3QuerySerializer.page_size).
const CALLS_LIMIT = 500;

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
};

const COLUMNS = [
  { id: "scenario", label: "Scenario", minWidth: 260 },
  { id: "verdict", label: "Verdict", width: 150 },
  {
    id: "failedOn",
    label: "Failed on",
    width: 180,
    hint: "The evaluations that failed it, the reason the scenario didn't pass.",
  },
];

// Collapses the latest run's calls into one row per scenario that failed at
// least one trial. A scenario that also passed a trial is flaky; the run's
// trials are the repeats. Errored calls never ran to a verdict, so they don't
// count against the agent (run_results_v3 EVALUATED_OUTCOMES).
function failingCasesFromCalls(tasks = []) {
  const byScenario = new Map();
  tasks.forEach((task) => {
    const key = task.sourceScenarioKey || task.sourceScenario || task.id;
    if (!byScenario.has(key)) byScenario.set(key, []);
    byScenario.get(key).push(task);
  });
  const cases = [];
  byScenario.forEach((calls, key) => {
    const failed = calls.filter((c) => c.status === "failed");
    if (failed.length === 0) return;
    const passed = calls.filter((c) => c.status === "passed").length;
    const failures = new Map();
    failed.forEach((c) =>
      (c.evalResults || [])
        .filter((e) => e.passed === false)
        .forEach((e) => {
          const name = e.name || e.id;
          if (!failures.has(name)) failures.set(name, new Set());
          if (e.reason) failures.get(name).add(e.reason);
        }),
    );
    const first = failed[0];
    cases.push({
      key,
      name: first.sourceScenario || first.scenario || "Untitled scenario",
      goal: first.goal || null,
      persona: first.persona || null,
      verdict: passed > 0 ? "flaky" : "failed",
      passed,
      failed: failed.length,
      evaluated: passed + failed.length,
      failedOn: [...failures.keys()],
      reasons: [...failures.entries()].flatMap(([name, reasons]) =>
        [...reasons].map((reason) => ({ name, reason })),
      ),
    });
  });
  return cases;
}

/**
 * Add scenarios from failing cases. Lists the scenarios that failed in this
 * environment's latest run, one row per scenario, with the evaluations that
 * failed them and why. Picking rows is UI only: the variations a pick would
 * generate aren't in the API yet, so adding saves nothing.
 */
export default function FailingCasesRoute({ env, envState, onClose }) {
  const { runs, isLoading: runsLoading } = useEnvironmentRuns(env, envState);
  const latest = runs?.[0] ?? null;
  const executionId = latest?.executionId ?? null;
  const {
    tasks,
    count: callCount = 0,
    isLoading: callsLoading,
    error,
  } = useRunCalls(executionId, {
    limit: CALLS_LIMIT,
    groupBy: "",
  });

  const cases = useMemo(() => failingCasesFromCalls(tasks), [tasks]);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState({});

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return cases;
    return cases.filter((k) =>
      [k.name, k.persona, ...k.failedOn]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [cases, query]);

  const chosen = shown.filter((k) => picked[k.key]);
  const toggle = (k) => setPicked((p) => ({ ...p, [k.key]: !p[k.key] }));
  const selectAll = () =>
    setPicked((p) => ({
      ...p,
      ...Object.fromEntries(shown.map((k) => [k.key, true])),
    }));
  const clear = () => setPicked({});
  const commit = () => {
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
    onClose?.();
  };

  if (runsLoading || (executionId && callsLoading)) {
    return (
      <Stack alignItems="center" sx={{ py: 8 }}>
        <CircularProgress size={24} />
      </Stack>
    );
  }

  // A client-seeded run carries no execution, so it has no calls to read.
  if (!latest || !executionId) {
    return (
      <EmptyState
        icon="solar:play-circle-linear"
        title="No runs yet"
        body="Failing cases come from this environment's runs. Run it first. Anything that fails shows up here to turn into new scenarios."
      />
    );
  }

  if (error) {
    return (
      <EmptyState
        icon="solar:danger-triangle-linear"
        title="Couldn't load the latest run"
        body="The calls of this environment's latest run didn't load. Try again in a moment."
      />
    );
  }

  const running = latest.runState === "running" || latest.status === "running";
  const runTestId = env?.platform?.runTestId;
  const runLink = runTestId
    ? paths.dashboard.simulate.environments.execution(
        env.id,
        runTestId,
        executionId,
      )
    : null;
  const runName = latest.label || `Run ${latest.ordinal}`;
  const runMeta = [
    runName,
    latest.agentVersion ? `agent ${latest.agentVersion}` : null,
    latest.finishedAt ? fToNow(latest.finishedAt) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const notes = [
    running
      ? `${runName} is still running. More failures may show up when it finishes.`
      : null,
    callCount > (tasks?.length ?? 0)
      ? `Showing failures from the first ${tasks.length} of ${callCount} calls.`
      : null,
  ].filter(Boolean);

  const flakyCount = cases.filter((k) => k.verdict === "flaky").length;
  const addLabel =
    chosen.length > 0
      ? `Add ${chosen.length} variation${chosen.length === 1 ? "" : "s"}`
      : "Add variations";

  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack sx={{ flex: 1, minWidth: 0 }} spacing={0.25}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Iconify
              icon="solar:play-circle-linear"
              width={14}
              sx={{ color: "text.subtitle" }}
            />
            <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
              {`Failing cases from ${runMeta}`}
            </Typography>
            <UiOnlyFlag feature="scenarioSources" />
          </Stack>
          {runLink && (
            <Box>
              <Button
                size="small"
                component={RouterLink}
                to={runLink}
                target="_blank"
                rel="noopener"
                endIcon={
                  <Iconify icon="solar:arrow-right-up-linear" width={14} />
                }
                sx={{ color: "text.secondary", ml: -0.75, typography: "s3" }}
              >
                {`Open ${runName}`}
              </Button>
            </Box>
          )}
        </Stack>
        {cases.length > 0 && (
          <Button
            variant="contained"
            color="primary"
            size="small"
            disabled={chosen.length === 0}
            onClick={commit}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            sx={{ whiteSpace: "nowrap", flexShrink: 0, minWidth: 148 }}
          >
            {addLabel}
          </Button>
        )}
      </Stack>

      {notes.map((note) => (
        <Typography
          key={note}
          sx={{
            typography: "s3",
            color: "text.subtitle",
            px: 2.5,
            py: 0.875,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          {note}
        </Typography>
      ))}

      {cases.length === 0 ? (
        <EmptyState
          icon="solar:check-circle-linear"
          title="Nothing failed in the latest run"
          body={`Every evaluated scenario in ${runName} passed. Failed and flaky scenarios show up here to turn into scenarios that reproduce them.`}
        />
      ) : (
        <>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 2.5,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <TextField
              size="small"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by scenario, persona or the evaluation it failed"
              InputProps={{
                sx: { typography: "s2" },
                startAdornment: (
                  <Box
                    sx={{
                      pr: 0.75,
                      pl: 0.25,
                      display: "flex",
                      color: "text.subtitle",
                    }}
                  >
                    <Iconify icon="solar:magnifer-linear" width={14} />
                  </Box>
                ),
              }}
              sx={{ flex: 1, minWidth: 220 }}
            />
          </Stack>

          <Stack
            direction="row"
            alignItems="center"
            sx={{
              px: 2.5,
              py: 0.875,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}
            >
              {`${shown.length} failing scenario${shown.length === 1 ? "" : "s"}`}
              {` · ${cases.length - flakyCount} failed, ${flakyCount} flaky`}
              {chosen.length > 0 && (
                <Box
                  component="span"
                  sx={{ color: "text.secondary", fontWeight: 600 }}
                >
                  {` · ${chosen.length} selected`}
                </Box>
              )}
            </Typography>
            <Button
              size="small"
              onClick={selectAll}
              disabled={shown.length === 0 || shown.every((k) => picked[k.key])}
              sx={{
                color: "primary.main",
                minWidth: 0,
                "&.Mui-disabled": { color: "text.disabled" },
              }}
            >
              Select all
            </Button>
            {chosen.length > 0 && (
              <Button
                size="small"
                onClick={clear}
                sx={{ color: "text.secondary", minWidth: 0, ml: 0.5 }}
              >
                Clear
              </Button>
            )}
          </Stack>

          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 2.5,
              py: 0.875,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}
            >
              Each pick would become variations with the same goal, kept only if
              they still fail on today&apos;s agent.
            </Typography>
            <SoonChip
              label="Variations"
              what="Generated variations of a failed scenario"
              plural
              size="tiny"
            />
          </Stack>

          {shown.length === 0 ? (
            <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                No failing scenarios match your search.
              </Typography>
            </Box>
          ) : (
            <CasesTable rows={shown} picked={picked} onToggle={toggle} />
          )}
        </>
      )}
    </Box>
  );
}

FailingCasesRoute.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func,
};

function CasesTable({ rows, picked, onToggle }) {
  return (
    <TableContainer sx={{ flex: 1, minHeight: 0 }}>
      <Table stickyHeader size="small" sx={{ minWidth: 640 }}>
        <TableHead>
          <TableRow
            sx={{
              "& .MuiTableCell-head": {
                bgcolor: "background.paper",
                backgroundImage: "none",
                // Sticky cells don't paint borders in a collapsed table, so a
                // shadow draws the divider.
                borderBottom: "none",
                boxShadow: (t) => `inset 0 -1px 0 ${t.palette.divider}`,
                py: 1.25,
                px: 1.5,
                whiteSpace: "nowrap",
              },
            }}
          >
            <TableCell padding="checkbox" sx={{ width: 40 }} />
            {COLUMNS.map((col) => (
              <TableCell
                key={col.id}
                sx={{ width: col.width, minWidth: col.minWidth }}
              >
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 500,
                      color: "text.secondary",
                    }}
                  >
                    {col.label}
                  </Typography>
                  {col.hint && (
                    <CustomTooltip show title={col.hint} arrow placement="top">
                      <Box
                        component="span"
                        sx={{
                          display: "flex",
                          color: "text.disabled",
                          cursor: "help",
                        }}
                      >
                        <Iconify icon="solar:info-circle-linear" width={14} />
                      </Box>
                    </CustomTooltip>
                  )}
                </Stack>
              </TableCell>
            ))}
            <TableCell sx={{ width: 40 }} />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((k) => (
            <CaseTableRow
              key={k.key}
              kase={k}
              picked={!!picked[k.key]}
              onToggle={() => onToggle(k)}
            />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
CasesTable.propTypes = {
  rows: PropTypes.array,
  picked: PropTypes.object,
  onToggle: PropTypes.func,
};

function CaseTableRow({ kase: k, picked, onToggle }) {
  const [open, setOpen] = useState(false);
  const toggleOpen = () => setOpen((o) => !o);
  const trials =
    k.evaluated > 1 ? `failed ${k.failed} of ${k.evaluated} trials` : null;
  const sub = [k.persona, trials].filter(Boolean).join(" · ");

  return (
    <>
      <TableRow
        hover
        selected={picked}
        onClick={toggleOpen}
        sx={{
          cursor: "pointer",
          height: 56,
          "&.Mui-selected, &.Mui-selected:hover": {
            bgcolor: "action.selected",
          },
          "& .MuiTableCell-body": {
            borderBottom: "1px solid",
            borderColor: "divider",
            px: 1.5,
            py: 0,
          },
        }}
      >
        <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
          <Checkbox
            size="small"
            checked={picked}
            onChange={onToggle}
            inputProps={{ "aria-label": `Select ${k.name}` }}
            sx={CHECKBOX_SX}
          />
        </TableCell>

        <TableCell>
          <Stack spacing={0.25} justifyContent="center">
            <Typography
              noWrap
              sx={{
                typography: "s2",
                fontWeight: 500,
                color: "text.primary",
                maxWidth: 420,
              }}
            >
              {k.name}
            </Typography>
            {sub && (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.subtitle", maxWidth: 360 }}
              >
                {sub}
              </Typography>
            )}
          </Stack>
        </TableCell>

        <TableCell>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: k.verdict === "failed" ? "error.main" : "warning.main",
            }}
          >
            {k.verdict === "failed"
              ? "Failed"
              : `Flaky, passed ${k.passed} of ${k.evaluated}`}
          </Typography>
        </TableCell>

        <TableCell>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: k.failedOn.length ? "text.secondary" : "text.disabled",
              maxWidth: 200,
            }}
          >
            {k.failedOn.length ? k.failedOn.join(", ") : "No evaluation failed"}
          </Typography>
        </TableCell>

        <TableCell onClick={(e) => e.stopPropagation()}>
          <CustomTooltip
            show
            title={open ? "Hide detail" : "Show detail"}
            arrow
          >
            <IconButton
              size="small"
              onClick={toggleOpen}
              aria-label={open ? "Hide detail" : "Show detail"}
              aria-expanded={open}
            >
              <Iconify
                icon={
                  open
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={14}
                sx={{ color: "text.subtitle" }}
              />
            </IconButton>
          </CustomTooltip>
        </TableCell>
      </TableRow>

      <TableRow
        sx={{
          "& .MuiTableCell-body": {
            borderBottom: open ? "1px solid" : "none",
            borderColor: "divider",
            p: 0,
          },
        }}
      >
        <TableCell colSpan={COLUMNS.length + 2} sx={{ py: "0 !important" }}>
          <Collapse in={open} unmountOnExit>
            <Stack
              spacing={1.5}
              sx={{ px: 6, py: 2, bgcolor: "background.neutral" }}
            >
              {k.goal && (
                <Box>
                  <Label>Goal</Label>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {k.goal}
                  </Typography>
                </Box>
              )}
              <Box>
                <Label>Why it failed</Label>
                {k.reasons.length ? (
                  <Stack spacing={0.5}>
                    {k.reasons.map(({ name, reason }) => (
                      <Typography
                        key={`${name}:${reason}`}
                        sx={{ typography: "s2", color: "text.secondary" }}
                      >
                        <Box component="span" sx={{ fontWeight: 600 }}>
                          {`${name}: `}
                        </Box>
                        {reason}
                      </Typography>
                    ))}
                  </Stack>
                ) : (
                  <Typography sx={{ typography: "s2", color: "text.disabled" }}>
                    The run gave no reason for this failure.
                  </Typography>
                )}
              </Box>
              <Box>
                <Label>Where it went wrong</Label>
                <SoonChip
                  label="Failing turn"
                  what="The turn and quote where the call went wrong"
                  size="tiny"
                />
              </Box>
            </Stack>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}
CaseTableRow.propTypes = {
  kase: PropTypes.object,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.subtitle",
        letterSpacing: 0.4,
        mb: 0.5,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
