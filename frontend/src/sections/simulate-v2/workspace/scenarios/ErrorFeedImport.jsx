import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
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
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import ErrorSeverityBadge from "src/pages/dashboard/error-feed/components/ErrorSeverityBadge";
import { useErrorFeedList } from "src/api/errorFeed/mockAdapter";
import { SectionCard } from "../../components/primitives";
import { useConnectedAgents } from "../../store";
import { errorFeedProjectsOf, reproduceOf } from "../../errorFeed/issueFix";
import { issueAsSource } from "../../_mock/errorFeedSource";
import {
  errorFeedScenarios,
  asProposal,
  failingStepOf,
} from "../../_mock/scenarioSources";
import { PersonalDataTag, ReproTag } from "./ScenarioStateBits";
import { agentIdOf, envAgentKey } from "../../_mock/agentRegistry";
import { clickable, focusRing } from "../../components/clickable";

/* Most severe first — the Error Feed's own order. */
const SEVERITIES = ["critical", "high", "medium", "low"];
const LIST_PARAMS = { limit: 100, sort_by: "last_seen", sort_dir: "desc" };

const SELECT_SX = {
  minWidth: 168,
  flexShrink: 0,
  "& .MuiSelect-select": { py: 0.75, pr: "28px !important", pl: 1.25 },
  "& .MuiSelect-icon": { color: "text.tertiary", right: 8 },
};

/**
 * Add scenarios from the Error Feed.
 *
 * The Error Feed groups failing production traces into issues. Picking an
 * issue adds the scenarios Fix would build from it — the representative
 * failure first, then the variations seen around it — so a real-world
 * failure becomes a permanent test the environment runs.
 *
 * Issues are scoped to the agent this environment tests: the Observe
 * projects its traces come from, and any project someone linked to it.
 * Every other project is one switch away, for an issue the feed couldn't
 * place.
 *
 * Each scenario keeps the issue and the traces it came from, so its badge
 * links back to the issue.
 */
export default function ErrorFeedImport({
  env,
  envState,
  onAdd,
  selected,
  onClose,
}) {
  const navigate = useNavigate();
  const { agents, state } = useConnectedAgents();
  /* The agent this environment tests, as the agents list knows it. */
  const agent = useMemo(
    () =>
      agents.find((a) => (a.usedIn || []).some((u) => u.envId === env?.id)) ||
      null,
    [agents, env],
  );
  const projects = useMemo(
    () => errorFeedProjectsOf(agent, state),
    [agent, state],
  );

  /* No retries: with no backend the mock list should show at once, not after
     three failed attempts. */
  const { data, isLoading } = useErrorFeedList(LIST_PARAMS, { retry: false });
  const allIssues = useMemo(() => data?.data || [], [data]);
  const ownIssues = useMemo(
    () => allIssues.filter((i) => projects.includes(i.project_id)),
    [allIssues, projects],
  );

  const [scope, setScope] = useState("agent");
  const [severity, setSeverity] = useState("all");
  const [query, setQuery] = useState("");
  /* { [issueId]: { [draftId]: true } } */
  const [picked, setPicked] = useState({});

  const scoped = scope === "agent" ? ownIssues : allIssues;
  /* Each issue's conversations as scenarios (RSI PRD): an inferred persona,
     the failing step as a sub-goal, personal data replaced, and what
     reproducing found on today's agent. Only reproduced ones become targets. */
  const issues = useMemo(
    () =>
      scoped.map((i) => {
        const src = issueAsSource(i, env, envState);
        const repro = reproduceOf(i, src.drafts);
        const drafts = errorFeedScenarios(src.drafts, i, { repro }).map((d) =>
          asProposal(d, "error-feed"),
        );
        const byId = new Map(drafts.map((d) => [d.id, d]));
        return {
          ...src,
          drafts,
          step: failingStepOf(i),
          reproduced: repro.reproduced,
          cases: src.cases.map((k) => ({
            ...k,
            scenario: byId.get(k.id),
            repro: byId.get(k.id)?.reproduce || null,
          })),
        };
      }),
    [scoped, env, envState],
  );

  const alreadyIn = useMemo(
    () => new Set(selected.map((s) => s.id)),
    [selected],
  );
  const isAdded = (c, k) => alreadyIn.has(k.id);
  const reproducible = (k) => !k.repro || k.repro.state === "reproduced";
  const openCases = (c) =>
    c.cases.filter((k) => !isAdded(c, k) && reproducible(k));

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return issues.filter((c) => {
      if (severity !== "all" && c.severity !== severity) return false;
      if (!q) return true;
      return `${c.id} ${c.title} ${c.type || ""}`.toLowerCase().includes(q);
    });
  }, [issues, severity, query]);
  const severities = SEVERITIES.filter((v) =>
    issues.some((c) => c.severity === v),
  );

  const pickedCases = (c) => openCases(c).filter((k) => picked[c.id]?.[k.id]);
  const chosen = shown
    .map((c) => ({ issue: c, cases: pickedCases(c) }))
    .filter((p) => p.cases.length > 0);
  const scenarioCount = chosen.reduce((n, p) => n + p.cases.length, 0);
  const importable = shown.filter((c) => openCases(c).length > 0);

  const allOf = (c) =>
    Object.fromEntries(openCases(c).map((k) => [k.id, true]));
  /* The issue checkbox picks every scenario it still has open, or — when
     they're all picked already — clears them. */
  const toggleIssue = (c) =>
    setPicked((p) => ({
      ...p,
      [c.id]: pickedCases(c).length === openCases(c).length ? {} : allOf(c),
    }));
  const toggleCase = (c, k) =>
    setPicked((p) => ({
      ...p,
      [c.id]: { ...p[c.id], [k.id]: !p[c.id]?.[k.id] },
    }));
  const selectAll = () => {
    const next = { ...picked };
    importable.forEach((c) => {
      next[c.id] = allOf(c);
    });
    setPicked(next);
  };
  const clear = () => setPicked({});

  const commit = () =>
    onAdd(
      chosen.flatMap(({ issue, cases }) => {
        const ids = new Set(cases.map((k) => k.id));
        return issue.drafts.filter((d) => ids.has(d.id));
      }),
    );

  if (isLoading && !allIssues.length) {
    return (
      <SectionCard
        sx={{ flex: 1, display: "grid", placeItems: "center", minHeight: 240 }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <CircularProgress
            size={14}
            thickness={5}
            sx={{ color: "text.disabled" }}
          />
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            Loading Error Feed issues…
          </Typography>
        </Stack>
      </SectionCard>
    );
  }

  /* Nothing from this agent's projects: say why, and offer the rest. */
  if (scope === "agent" && ownIssues.length === 0) {
    const envAgent =
      envState?.agent && !envState.agent.seeded ? envState.agent : null;
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
          icon="solar:bug-linear"
          width={26}
          sx={{ color: "text.disabled" }}
        />
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {!(agent || envAgent)
            ? "Connect your own agent first"
            : projects.length
              ? "No open issues from this agent"
              : "Not linked to an Observe project yet"}
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.tertiary", maxWidth: 520 }}
        >
          {!(agent || envAgent)
            ? "This environment still tests its template baseline. Error Feed issues come from your agent's real calls, once it's connected and watched."
            : projects.length
              ? `Nothing open in ${projects.join(", ")} right now. New issues from it show up here.`
              : "Error Feed groups issues by the Observe project traces land in. Link the agent's project — or watch its production calls — and its issues show up here."}
        </Typography>
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          {envAgent && !projects.length && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => {
                onClose?.();
                navigate(
                  `${paths.dashboard.agents}/connected/${agentIdOf(envAgentKey(envAgent, env.id))}?tab=connections`,
                );
              }}
            >
              Link a project
            </Button>
          )}
          {allIssues.length > 0 && (
            <Button
              size="small"
              onClick={() => setScope("all")}
              sx={{ color: "text.secondary" }}
            >
              {`Browse every project (${allIssues.length})`}
            </Button>
          )}
        </Stack>
      </Stack>
    );
  }

  return (
    /*
      Same frame as the Error Feed list: a toolbar row with the primary
      action inline on the right, a slim meta strip, then the table.
    */
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
          placeholder="Search issues by id, title or error type…"
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
          value={scope}
          onChange={(e) => {
            setScope(e.target.value);
            setPicked({});
          }}
          IconComponent={(p) => (
            <Iconify {...p} icon="solar:alt-arrow-down-linear" width={12} />
          )}
          renderValue={(v) => (
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {v === "agent" ? "This agent's projects" : "Every project"}
            </Typography>
          )}
          sx={SELECT_SX}
        >
          <MenuItem value="agent" sx={{ typography: "s2" }}>
            {`This agent's projects${projects.length ? ` · ${projects.join(", ")}` : ""}`}
          </MenuItem>
          <MenuItem value="all" sx={{ typography: "s2" }}>
            Every project
          </MenuItem>
        </Select>
        <Select
          size="small"
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          IconComponent={(p) => (
            <Iconify {...p} icon="solar:alt-arrow-down-linear" width={12} />
          )}
          renderValue={(v) => (
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Iconify
                icon="solar:filter-linear"
                width={14}
                sx={{ color: "text.tertiary" }}
              />
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {v === "all"
                  ? "Any severity"
                  : v.charAt(0).toUpperCase() + v.slice(1)}
              </Typography>
            </Stack>
          )}
          sx={{ ...SELECT_SX, minWidth: 148 }}
        >
          <MenuItem value="all" sx={{ typography: "s2" }}>
            Any severity
          </MenuItem>
          {severities.map((v) => (
            <MenuItem key={v} value={v} sx={{ typography: "s2" }}>
              {v.charAt(0).toUpperCase() + v.slice(1)}
            </MenuItem>
          ))}
        </Select>
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={chosen.length === 0}
          onClick={commit}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{
            whiteSpace: "nowrap",
            flexShrink: 0,
            minWidth: 148,
          }}
        >
          {scenarioCount > 0
            ? `Approve ${scenarioCount} scenario${scenarioCount === 1 ? "" : "s"}`
            : "Approve scenarios"}
        </Button>
      </Stack>

      {/* meta strip — quiet count on the left, bulk-action text-links on the right */}
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
          {query || severity !== "all"
            ? `${shown.length} of ${issues.length} issues`
            : `${issues.length} issue${issues.length === 1 ? "" : "s"}`}
          {chosen.length > 0 && (
            <Box
              component="span"
              sx={{ color: "text.secondary", fontWeight: 600 }}
            >
              {" · "}
              {chosen.length} selected
              {" · "}
              {scenarioCount} scenario{scenarioCount === 1 ? "" : "s"}
            </Box>
          )}
        </Typography>
        <Button
          size="small"
          onClick={selectAll}
          disabled={
            importable.length === 0 ||
            importable.every(
              (c) => pickedCases(c).length === openCases(c).length,
            )
          }
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
        Each conversation is run three times on today&apos;s agent first. Only
        reproduced ones become targets — inconclusive and not reproduced ones
        are shown, not added. Names, numbers, emails and addresses are replaced
        with fake values before saving; call audio is never reused.
      </Typography>

      {/* table — mirrors the Error Feed table shape (checkbox · issue · severity · evidence · traces · last seen · expand) */}
      {shown.length === 0 ? (
        <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            No issues match your filters.
          </Typography>
        </Box>
      ) : (
        <IssuesTable
          rows={shown}
          picked={picked}
          isAdded={isAdded}
          onToggleIssue={toggleIssue}
          onToggleCase={toggleCase}
        />
      )}
    </SectionCard>
  );
}

ErrorFeedImport.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onAdd: PropTypes.func,
  onClose: PropTypes.func,
  selected: PropTypes.array,
};

/* ── table ───────────────────────────────────────────────────────────────── */

const COLUMNS = [
  { id: "issue", label: "Issue", minWidth: 320 },
  { id: "severity", label: "Severity", width: 100 },
  { id: "evidence", label: "Evidence", width: 110 },
  { id: "traces", label: "Traces", width: 72, align: "right" },
  {
    id: "scenarios",
    label: "Scenarios",
    width: 92,
    align: "right",
    hint: "One per distinct situation in the issue's traces. Traces that fail the same way in the same situation are the same test, so they become one scenario.",
  },
  { id: "lastSeen", label: "Last seen", width: 110 },
];

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

/**
 * Deliberately shaped to mirror the Error Feed table (sticky compact
 * header, checkbox column, two-line issue cell with the issue id tag
 * beneath the title, severity dot-badge, right-aligned trace count) — so
 * this pane reads as the same object the user sees in the Error Feed,
 * just filtered to what they can add.
 */
function IssuesTable({ rows, picked, isAdded, onToggleIssue, onToggleCase }) {
  return (
    <TableContainer sx={{ flex: 1, minHeight: 0 }}>
      <Table stickyHeader size="small" sx={{ minWidth: 860 }}>
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
                align={col.align || "left"}
                sx={{ width: col.width, minWidth: col.minWidth }}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  justifyContent={
                    col.align === "right" ? "flex-end" : "flex-start"
                  }
                >
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
          {rows.map((c) => (
            <IssueTableRow
              key={c.id}
              issue={c}
              pickedMap={picked[c.id] || {}}
              isAdded={isAdded}
              onToggle={() => onToggleIssue(c)}
              onToggleCase={(k) => onToggleCase(c, k)}
            />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
IssuesTable.propTypes = {
  rows: PropTypes.array,
  picked: PropTypes.object,
  isAdded: PropTypes.func,
  onToggleIssue: PropTypes.func,
  onToggleCase: PropTypes.func,
};

function IssueTableRow({ issue, pickedMap, isAdded, onToggle, onToggleCase }) {
  const [open, setOpen] = useState(false);
  const c = issue;

  const notAdded = c.cases.filter((k) => !isAdded(c, k));
  const remaining = notAdded.filter(
    (k) => !k.repro || k.repro.state === "reproduced",
  );
  const addedCount = c.cases.length - notAdded.length;
  const alreadyIn = notAdded.length === 0;
  const noneReproduced = !alreadyIn && remaining.length === 0;
  const pickedCount = remaining.filter((k) => pickedMap[k.id]).length;
  const allPicked = !alreadyIn && pickedCount === remaining.length;
  const somePicked = pickedCount > 0 && !allPicked;
  const rowSelected = pickedCount > 0;

  return (
    <>
      <TableRow
        hover
        selected={rowSelected}
        /* Clicking the row opens its detail; only the checkbox selects —
           so reading an issue never quietly adds it. */
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
          <Checkbox
            size="small"
            checked={allPicked || alreadyIn}
            indeterminate={somePicked}
            disabled={alreadyIn || noneReproduced}
            onChange={onToggle}
            sx={CHECKBOX_SX}
          />
        </TableCell>

        {/* issue — title + id tag (mirrors the Error Feed's "Error name + type" cell) */}
        <TableCell>
          <Stack direction="column" spacing={0.5} justifyContent="center">
            <Tooltip title={c.title} placement="top-start" arrow>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 500,
                  color: "text.primary",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 460,
                }}
              >
                {c.title}
              </Typography>
            </Tooltip>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <FingerprintTag value={c.id} />
              {c.type && (
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    maxWidth: 200,
                  }}
                >
                  {c.type}
                </Typography>
              )}
              {c.critical && (
                <Typography
                  sx={{
                    fontSize: "12px",
                    fontWeight: 600,
                    letterSpacing: 0.4,
                    color: "status.danger.fg",
                    textTransform: "none",
                  }}
                >
                  Critical
                </Typography>
              )}
              {addedCount > 0 && (
                <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                  {alreadyIn
                    ? "· already added"
                    : `· ${addedCount} of ${c.cases.length} already added`}
                </Typography>
              )}
              {!alreadyIn && (
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {noneReproduced
                    ? "· didn't reproduce"
                    : `· ${remaining.length} of ${notAdded.length} reproduced`}
                </Typography>
              )}
            </Stack>
          </Stack>
        </TableCell>

        <TableCell>
          <ErrorSeverityBadge severity={c.severity} />
        </TableCell>

        <TableCell>
          <Typography sx={{ typography: "s3", color: "text.secondary" }} noWrap>
            {c.evidence || "—"}
          </Typography>
        </TableCell>

        <TableCell align="right">
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 500,
              color: "text.primary",
              fontFeatureSettings: "'tnum'",
            }}
          >
            {c.count}
          </Typography>
        </TableCell>

        {/* how many scenarios this issue turns into — "2 of 3" once you've
            dropped a case, so the count never hides a partial pick */}
        <TableCell align="right">
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 500,
              fontFeatureSettings: "'tnum'",
              color: alreadyIn ? "text.disabled" : "text.primary",
            }}
          >
            {somePicked
              ? `${pickedCount} of ${remaining.length}`
              : alreadyIn
                ? c.cases.length
                : remaining.length}
          </Typography>
        </TableCell>

        <TableCell>
          <Typography sx={{ typography: "s3", color: "text.disabled" }} noWrap>
            {c.lastSeen}
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

      {/* full-width detail row — un-hoverable, no border above so it feels attached to the parent */}
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
              {c.why && (
                <Box>
                  <Label>Why it&apos;s in the feed</Label>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {c.why}
                  </Typography>
                </Box>
              )}
              <Box>
                <Stack
                  direction="row"
                  alignItems="baseline"
                  spacing={0.75}
                  sx={{ mb: 0.5 }}
                >
                  <Label sx={{ mb: 0 }}>
                    {`Becomes ${c.cases.length} scenario${c.cases.length === 1 ? "" : "s"}`}
                  </Label>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {c.cases.length === 1
                      ? `— all ${c.count} traces failed in the same situation`
                      : `— one per distinct situation across its ${c.count} traces`}
                  </Typography>
                </Stack>
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    overflow: "hidden",
                  }}
                >
                  {c.cases.map((k, i) => (
                    <CaseRow
                      key={k.id}
                      kase={k}
                      first={i === 0}
                      added={isAdded(c, k)}
                      picked={!!pickedMap[k.id]}
                      onToggle={() => onToggleCase(k)}
                    />
                  ))}
                </Box>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                >
                  {`Each keeps the issue's goal, with the failing step as a critical sub-goal: “${c.step}”. The caller's persona is inferred from the conversation.`}
                </Typography>
              </Box>
              {/* The way back to the issue itself — the traces, trends and analysis. */}
              <Box>
                <Button
                  size="small"
                  component={RouterLink}
                  to={paths.dashboard.errorFeed.detail(c.id)}
                  target="_blank"
                  rel="noopener"
                  endIcon={
                    <Iconify icon="solar:arrow-right-up-linear" width={14} />
                  }
                  sx={{ color: "text.secondary", ml: -0.75 }}
                >
                  {`Open ${c.id} in the Error Feed`}
                </Button>
              </Box>
            </Stack>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}
IssueTableRow.propTypes = {
  issue: PropTypes.object,
  pickedMap: PropTypes.object,
  isAdded: PropTypes.func,
  onToggle: PropTypes.func,
  onToggleCase: PropTypes.func,
};

/**
 * One scenario an issue becomes — the situation, the traces it was built
 * from, and how many traces it covers. Each can be dropped without
 * dropping the issue.
 */
function CaseRow({ kase, first, added, picked, onToggle }) {
  /* Only reproduced conversations become targets. */
  const blocked = !added && kase.repro && kase.repro.state !== "reproduced";
  const persona = kase.scenario?.persona;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        px: 1,
        py: 0.75,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
      }}
    >
      {/* Same rule as the cluster rows: only the checkbox selects. */}
      <Checkbox
        size="small"
        checked={(picked && !blocked) || added}
        disabled={added || blocked}
        onChange={onToggle}
        sx={CHECKBOX_SX}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0 }}
        >
          <Typography
            noWrap
            sx={{
              typography: "s2",
              fontWeight: 500,
              color: added || blocked ? "text.disabled" : "text.primary",
              minWidth: 0,
            }}
          >
            {kase.label}
          </Typography>
          {kase.repro && <ReproTag repro={kase.repro} target={!blocked} />}
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0, mt: 0.25 }}
        >
          {persona && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
            >
              {`${persona.name} · ${persona.archetype} (inferred)`}
            </Typography>
          )}
          {kase.scenario && <PersonalDataTag row={kase.scenario} />}
        </Stack>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.tertiary",
            mt: 0.25,
          }}
        >
          {kase.quote}
        </Typography>
      </Box>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          fontFeatureSettings: "'tnum'",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        {added
          ? "already added"
          : `${kase.traceCount} trace${kase.traceCount === 1 ? "" : "s"}`}
      </Typography>
    </Stack>
  );
}
CaseRow.propTypes = {
  kase: PropTypes.object,
  first: PropTypes.bool,
  added: PropTypes.bool,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
};

/**
 * Fingerprint tag — same visual treatment as the Error Feed's Error
 * Type tag (small neutral pill) so the two lists read alike.
 */
function FingerprintTag({ value }) {
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        height: 18,
        borderRadius: "4px",
        px: "5px",
        bgcolor: "action.selected",
        color: "text.secondary",
        maxWidth: 260,
        overflow: "hidden",
      }}
    >
      <Typography
        sx={{
          fontSize: "12px",
          fontWeight: 500,
          fontFamily: "ui-monospace, Menlo, monospace",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}
FingerprintTag.propTypes = { value: PropTypes.string };

function Label({ children, sx }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        color: "text.tertiary",
        textTransform: "none",
        letterSpacing: 0.4,
        mb: 0.5,
        ...sx,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node, sx: PropTypes.object };
