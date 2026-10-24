import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Collapse,
  IconButton,
  Tooltip,
  TextField,
  Select,
  MenuItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SectionCard } from "../../components/primitives";
import {
  failingCasesOf,
  failingCaseScenarioId,
  scenarioFromFailingCase,
} from "../../_mock/failingCases";
import { relativeTime } from "../../_mock/scenarioProvenance";
import {
  failingCaseVariations,
  variationCheck,
  asProposal,
  VARIATIONS_PER_CASE,
} from "../../_mock/scenarioSources";
import { currentAgentVersion } from "../../_mock/versions";
import { clickable, focusRing } from "../../components/clickable";
import { scenarioTitle } from "../../_mock/scenarios";

const SELECT_SX = {
  minWidth: 168,
  flexShrink: 0,
  "& .MuiSelect-select": { py: 0.75, pr: "28px !important", pl: 1.25 },
  "& .MuiSelect-icon": { color: "text.tertiary", right: 8 },
};

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
};

/**
 * Add scenarios from failing cases.
 *
 * The runs of this environment already say which scenarios failed and on
 * which grader. Picking one generates new variations of it (RSI PRD) — a
 * different persona, different wording, different world values; the same
 * goal and sub-goals — grouped under their source scenario, so a fix has to
 * get past the failure, not just the one conversation that showed it. Flaky
 * ones count too: passing two of three is the failure a single run hides.
 *
 * Each variation is run on the agent version under test first. It must
 * still fail there; one that now passes reads "Already fixed" and isn't
 * added. The rest arrive as proposals, each linking back to
 * the run and the task it came from.
 */
export default function FailingCasesImport({ env, envState, onAdd, selected }) {
  const cases = useMemo(() => failingCasesOf(env, envState), [env, envState]);
  const agentNow = currentAgentVersion(envState)?.label || null;
  /* Each case's variations and how each one did on today's agent. */
  const variationsOf = useMemo(() => {
    const memo = new Map();
    return (k) => {
      if (!memo.has(k.key)) {
        const base = scenarioFromFailingCase(k, env);
        memo.set(
          k.key,
          failingCaseVariations(base, {
            source: k.scenario,
            env,
            envState,
          }).map((v) => ({ row: v, check: variationCheck(v, k, envState) })),
        );
      }
      return memo.get(k.key);
    };
  }, [env, envState]);
  const runs = useMemo(() => {
    const seen = new Map();
    cases.forEach((k) => {
      if (!seen.has(k.runId)) seen.set(k.runId, k);
    });
    return [...seen.values()];
  }, [cases]);
  const runCount = (envState?.runs || []).filter((r) => !r.synthetic).length;

  /* Newest run first — the failures the current agent version still has. */
  const [runId, setRunId] = useState(() => runs[0]?.runId || "all");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState({});

  const alreadyIn = useMemo(
    () => new Set(selected.map((s) => s.id)),
    [selected],
  );
  /* Added before — the older single reproduction, or any of its variations. */
  const isAdded = (k) =>
    alreadyIn.has(failingCaseScenarioId(k)) ||
    variationsOf(k).some((v) => alreadyIn.has(v.row.id));
  const stillFailing = (k) =>
    variationsOf(k).filter((v) => v.check.state === "fails");

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return cases.filter((k) => {
      if (runId !== "all" && k.runId !== runId) return false;
      if (!q) return true;
      return `${k.name} ${k.title || ""} ${k.evalName || ""}`
        .toLowerCase()
        .includes(q);
    });
  }, [cases, runId, query]);

  const chosen = shown.filter(
    (k) => picked[k.key] && !isAdded(k) && stillFailing(k).length > 0,
  );
  const open = shown.filter((k) => !isAdded(k) && stillFailing(k).length > 0);
  const toggle = (k) => setPicked((p) => ({ ...p, [k.key]: !p[k.key] }));
  const selectAll = () =>
    setPicked((p) => ({
      ...p,
      ...Object.fromEntries(open.map((k) => [k.key, true])),
    }));
  const clear = () => setPicked({});
  /* Only the variations that still fail are added, as proposals. */
  const toAdd = chosen.flatMap((k) => stillFailing(k));
  const fixedCount = chosen.reduce(
    (n, k) => n + variationsOf(k).length - stillFailing(k).length,
    0,
  );
  const commit = () =>
    onAdd(
      toAdd.map(({ row, check }) =>
        asProposal(
          {
            ...row,
            target: true,
            variationCheck: {
              state: check.state,
              fails: check.fails,
              of: check.of,
              agentVersion: check.agentVersion,
            },
          },
          "failing-case",
        ),
      ),
    );

  if (cases.length === 0) {
    return (
      <Stack
        alignItems="center"
        spacing={1}
        sx={{
          py: 6,
          px: 3,
          textAlign: "center",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
        }}
      >
        <Iconify
          icon="solar:play-circle-linear"
          width={26}
          sx={{ color: "text.disabled" }}
        />
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {runCount ? "Nothing failed yet" : "No runs yet"}
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.tertiary", maxWidth: 520 }}
        >
          {runCount
            ? `Every scenario passed in ${runCount === 1 ? "this environment's run" : `all ${runCount} of this environment's runs`}. Failed and flaky scenarios show up here to turn into scenarios that reproduce them.`
            : "Failing cases come from this environment's runs. Run the baseline first — anything that fails or comes out flaky shows up here."}
        </Typography>
      </Stack>
    );
  }

  return (
    <SectionCard
      sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
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
        <TextField
          size="small"
          fullWidth
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by scenario or the evaluation it failed…"
          InputProps={{
            sx: { typography: "s2" },
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
          sx={{ flex: 1, minWidth: 220 }}
        />
        <Select
          size="small"
          value={runId}
          onChange={(e) => setRunId(e.target.value)}
          IconComponent={(p) => (
            <Iconify {...p} icon="solar:alt-arrow-down-linear" width={12} />
          )}
          renderValue={(v) => {
            const r = runs.find((x) => x.runId === v);
            return (
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Iconify
                  icon="solar:play-circle-linear"
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {r ? `Run ${r.runOrdinal}` : "Every run"}
                </Typography>
              </Stack>
            );
          }}
          sx={SELECT_SX}
        >
          {runs.map((r) => (
            <MenuItem key={r.runId} value={r.runId} sx={{ typography: "s2" }}>
              {`Run ${r.runOrdinal} · agent ${r.agentVersion}${r.finishedAt ? ` · ${relativeTime(r.finishedAt)}` : ""}`}
            </MenuItem>
          ))}
          <MenuItem value="all" sx={{ typography: "s2" }}>
            Every run
          </MenuItem>
        </Select>
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={toAdd.length === 0}
          onClick={commit}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ whiteSpace: "nowrap", flexShrink: 0, minWidth: 148 }}
        >
          {toAdd.length > 0
            ? `Add ${toAdd.length} variation${toAdd.length === 1 ? "" : "s"}`
            : "Add variations"}
        </Button>
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
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {`${shown.length} failing case${shown.length === 1 ? "" : "s"}`}
          {` · ${shown.filter((k) => k.verdict === "failed").length} failed, ${shown.filter((k) => k.verdict === "flaky").length} flaky`}
          {chosen.length > 0 && (
            <Box
              component="span"
              sx={{ color: "text.secondary", fontWeight: 600 }}
            >
              {" · "}
              {chosen.length} selected · {toAdd.length} variation
              {toAdd.length === 1 ? "" : "s"} still fail
              {toAdd.length === 1 ? "s" : ""}
              {agentNow ? ` on ${agentNow}` : ""}
              {fixedCount > 0 && ` · ${fixedCount} already fixed, not added`}
            </Box>
          )}
        </Typography>
        <Button
          size="small"
          onClick={selectAll}
          disabled={open.length === 0 || open.every((k) => picked[k.key])}
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

      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          px: 2.5,
          py: 0.875,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        {`Each pick becomes ${VARIATIONS_PER_CASE} new variations — a different caller, wording and world values, the same goal and sub-goals — grouped under the scenario they came from. Each is run on ${agentNow ? `agent ${agentNow}` : "today's agent"} first; only the ones that still fail are proposed, for you to add.`}
      </Typography>

      {shown.length === 0 ? (
        <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            No failing cases match your search.
          </Typography>
        </Box>
      ) : (
        <CasesTable
          rows={shown}
          env={env}
          picked={picked}
          isAdded={isAdded}
          onToggle={toggle}
          variationsOf={variationsOf}
        />
      )}
    </SectionCard>
  );
}

FailingCasesImport.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onAdd: PropTypes.func,
  selected: PropTypes.array,
};

/* ── table ───────────────────────────────────────────────────────────────── */

const COLUMNS = [
  { id: "scenario", label: "Scenario", minWidth: 300 },
  { id: "verdict", label: "Verdict", width: 120 },
  {
    id: "failedOn",
    label: "Failed on",
    width: 160,
    hint: "The evaluation that failed it — the reason the scenario didn't pass.",
  },
  { id: "run", label: "Run", width: 150 },
];

/** Same frame as the Error Feed picker beside it: compact sticky header, checkbox column, detail on click. */
function CasesTable({ rows, env, picked, isAdded, onToggle, variationsOf }) {
  return (
    <TableContainer sx={{ flex: 1, minHeight: 0 }}>
      <Table stickyHeader size="small" sx={{ minWidth: 820 }}>
        <TableHead>
          <TableRow
            sx={{
              /* The product's DataTable header: no fill, the theme's grey gradient off. */
              "& .MuiTableCell-head": {
                bgcolor: "background.paper",
                backgroundImage: "none",
                /* Sticky cells don't paint borders in a collapsed table — a shadow draws the divider. */
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
                      fontSize: 13,
                      fontWeight: 500,
                      color: "text.secondary",
                    }}
                  >
                    {col.label}
                  </Typography>
                  {col.hint && (
                    <Tooltip title={col.hint} arrow placement="top">
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
                    </Tooltip>
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
              env={env}
              added={isAdded(k)}
              picked={!!picked[k.key]}
              onToggle={() => onToggle(k)}
              variations={variationsOf(k)}
            />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
CasesTable.propTypes = {
  rows: PropTypes.array,
  env: PropTypes.object,
  picked: PropTypes.object,
  isAdded: PropTypes.func,
  onToggle: PropTypes.func,
  variationsOf: PropTypes.func,
};

function CaseTableRow({ kase, env, added, picked, onToggle, variations = [] }) {
  const [open, setOpen] = useState(false);
  const k = kase;
  const failing = variations.filter((v) => v.check.state === "fails").length;
  /* Line 2 says which variation of the scenario failed; the counts follow it. */
  const sub = k.summary && k.summary !== (k.title || k.name) ? k.summary : null;
  const dot = sub || k.critical ? "· " : "";

  return (
    <>
      <TableRow
        hover
        selected={picked && !added && failing > 0}
        /* Clicking the row opens its detail; only the checkbox selects. */
        {...clickable(() => setOpen((o) => !o), {
          role: "row",
          expanded: open,
        })}
        sx={{
          ...focusRing,
          cursor: "pointer",
          height: 56,
          "&.Mui-selected, &.Mui-selected:hover": {
            bgcolor: "status.discovery.bg",
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
          <Tooltip
            arrow
            title={
              !added && failing === 0
                ? "Already fixed — every variation passes on today's agent, so there's nothing to add."
                : ""
            }
          >
            <span>
              <Checkbox
                size="small"
                checked={(picked && failing > 0) || added}
                disabled={added || failing === 0}
                onChange={onToggle}
                sx={CHECKBOX_SX}
              />
            </span>
          </Tooltip>
        </TableCell>

        <TableCell>
          <Stack spacing={0.25} justifyContent="center">
            <Typography
              noWrap
              sx={{
                typography: "s2",
                fontWeight: 500,
                color: added ? "text.disabled" : "text.primary",
                maxWidth: 440,
              }}
            >
              {scenarioTitle(k.title) || k.name}
            </Typography>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              {/* The variation, not the title again (the name is now the title). */}
              {sub && (
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    maxWidth: 320,
                  }}
                >
                  {sub}
                </Typography>
              )}
              {k.critical && (
                <Tooltip arrow title="Written as critical">
                  <Box sx={{ display: "flex", color: "status.danger.fg" }}>
                    <Iconify icon="solar:danger-triangle-bold" width={12} />
                  </Box>
                </Tooltip>
              )}
              {added && (
                <Typography
                  sx={{ typography: "s3", color: "text.disabled" }}
                >{`${dot}already added`}</Typography>
              )}
              {!added && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {failing === variations.length
                    ? `${dot}${failing} variations`
                    : failing === 0
                      ? `${dot}already fixed`
                      : `${dot}${failing} of ${variations.length} variations still fail`}
                </Typography>
              )}
            </Stack>
          </Stack>
        </TableCell>

        {/* Failed is the one verdict that earns colour; flaky says its count. */}
        <TableCell>
          <Typography
            noWrap
            sx={{
              typography: "s3",
              fontWeight: 600,
              color:
                k.verdict === "failed" ? "status.danger.fg" : "text.secondary",
            }}
          >
            {k.verdict === "failed"
              ? "Failed"
              : `Flaky · ${k.passes} of ${k.repeats}`}
          </Typography>
        </TableCell>

        <TableCell>
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {k.evalName || "—"}
          </Typography>
        </TableCell>

        <TableCell>
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {`Run ${k.runOrdinal} · ${k.agentVersion}`}
            {k.finishedAt && (
              <Box
                component="span"
                sx={{ color: "text.disabled" }}
              >{` · ${relativeTime(k.finishedAt)}`}</Box>
            )}
          </Typography>
        </TableCell>

        <TableCell>
          <Tooltip title={open ? "Hide detail" : "Show detail"} arrow>
            <IconButton size="small">
              <Iconify
                icon={
                  open
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={14}
                sx={{ color: "text.tertiary" }}
              />
            </IconButton>
          </Tooltip>
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
              {k.reason && (
                <Box>
                  <Label>Why it failed</Label>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {k.reason}
                  </Typography>
                </Box>
              )}
              {k.quote && (
                <Box>
                  <Label>
                    {k.turn
                      ? `Where it went wrong · turn ${k.turn}`
                      : "Where it went wrong"}
                  </Label>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontFamily: "ui-monospace, Menlo, monospace",
                      color: "text.secondary",
                    }}
                  >
                    {k.quote}
                  </Typography>
                </Box>
              )}
              <Box>
                <Label>{`Becomes ${variations.length} variations · scenario group ${k.scenario?.name || k.name}`}</Label>
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                    overflow: "hidden",
                    bgcolor: "background.paper",
                  }}
                >
                  {variations.map(({ row, check }, i) => {
                    const fixed = check.state === "fixed";
                    return (
                      <Stack
                        key={row.id}
                        direction="row"
                        alignItems="center"
                        spacing={1.5}
                        sx={{
                          px: 1.5,
                          py: 0.875,
                          borderTop: i ? "1px solid" : "none",
                          borderColor: "divider",
                        }}
                      >
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Typography
                            noWrap
                            sx={{
                              typography: "s2",
                              fontWeight: 500,
                              color: fixed ? "text.disabled" : "text.primary",
                            }}
                          >
                            {[row.persona?.name, row.variation?.wording]
                              .filter(Boolean)
                              .join(" · ")}
                          </Typography>
                          <Typography
                            noWrap
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            {row.situation}
                          </Typography>
                        </Box>
                        <Tooltip arrow title={check.note}>
                          <Typography
                            noWrap
                            sx={{
                              typography: "s3",
                              fontWeight: 600,
                              flexShrink: 0,
                              cursor: "help",
                              color: fixed ? "text.disabled" : "text.secondary",
                            }}
                          >
                            {fixed
                              ? "Already fixed"
                              : `${check.label} · ${check.fails} of ${check.of}`}
                          </Typography>
                        </Tooltip>
                      </Stack>
                    );
                  })}
                </Box>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                >
                  Same goal, pass condition and sub-goals as the scenario that
                  failed. Already fixed ones pass on today&apos;s agent and
                  aren&apos;t added.
                </Typography>
              </Box>
              <Box>
                <Button
                  size="small"
                  component={RouterLink}
                  to={paths.dashboard.simulate.simulationRun(env.id, k.runId)}
                  target="_blank"
                  rel="noopener"
                  endIcon={
                    <Iconify icon="solar:arrow-right-up-linear" width={14} />
                  }
                  sx={{ color: "text.secondary", ml: -0.75 }}
                >
                  {`Open run ${k.runOrdinal}`}
                </Button>
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
  env: PropTypes.object,
  added: PropTypes.bool,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
  variations: PropTypes.array,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        textTransform: "none",
        letterSpacing: 0.4,
        mb: 0.5,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
